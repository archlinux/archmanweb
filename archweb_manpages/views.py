import copy
import operator
from functools import reduce

from django.shortcuts import render
from django.http import HttpResponse, Http404, HttpResponseRedirect
from django.db.models import Count, Q
from django.contrib.postgres.search import TrigramSimilarity, SearchQuery, SearchVector, SearchHeadline, SearchRank

from .models import Package, Content, ManPage, SymbolicLink, UpdateLog, SoelimError
from .utils import reverse_man_url, paginate, extract_headings
from .forms import SearchForm

def index(request):
    count_man_pages = ManPage.objects.count()
    count_symlinks = SymbolicLink.objects.count()
    count_all_pkgs = Package.objects.count()
    count_pkgs_with_mans = ManPage.objects.aggregate(Count("package_id", distinct=True))["package_id__count"]
    last_updates = UpdateLog.objects.order_by("-id")[:5]
    context = {
        "count_man_pages": count_man_pages,
        "count_symlinks": count_symlinks,
        "count_pkgs_with_mans": count_pkgs_with_mans,
        "count_pkgs_without_mans": count_all_pkgs - count_pkgs_with_mans,
        "last_updates": last_updates,
    }
    return render(request, "index.html", context)

def simple_view(request, *, template_name):
    if template_name not in {"about", "dev"}:
        raise Http404()
    return render(request, "{}.html".format(template_name), {})

def listing(request, *, repo=None, pkgname=None):
    sorting = request.GET.get("sorting", "alphabetical")
    lang = request.GET.get("lang")
    section = request.GET.get("section")

    if sorting == "alphabetical":
        sorting_columns = ("name", "lang", "section")
    elif sorting == "-alphabetical":
        sorting_columns = ("-name", "-lang", "-section")
    elif sorting == "section":
        sorting_columns = ("section", "name", "lang")
    elif sorting == "-section":
        sorting_columns = ("-section", "-name", "-lang")
    elif sorting == "lang":
        sorting_columns = ("lang", "name", "section")
    elif sorting == "-lang":
        sorting_columns = ("-lang", "-name", "-section")
    else:
        raise HttpResponse("Unknown sorting parameter: {}".format(sorting), status=400)

    db_pkg = None
    man_pages = ManPage.objects.order_by( *sorting_columns )

    if pkgname:
        # check that such package exists
        if repo:
            query = Package.objects.filter(name=pkgname, repo=repo)
        else:
            query = Package.objects.filter(name=pkgname)
        if len(query) == 0:
            if repo:
                raise Http404("The package {} does not exist in the {} repository.".format(pkgname, repo))
            else:
                raise Http404("The package {} does not exist in the database.".format(pkgname))
        elif len(query) == 1:
            db_pkg = query[0]
        else:
            raise HttpResponse(
                    "The package {} exists in multiple repositories ({}) and ambiguous listings are not implemented."
                    .format(pkgname, ", ".join(pkg.repo for pkg in query)),
                    status=501)
        man_pages = man_pages.filter(package__name=pkgname)
    if lang:
        man_pages = man_pages.filter(lang=lang)
    if section:
        man_pages = man_pages.filter(section=section)

    # list of symbolic links in a package
    if pkgname:
        symlinks_sorting_columns = []
        for c in sorting_columns:
            if "name" in c:
                c = c.replace("name", "from_name")
            elif "section" in c:
                c = c.replace("section", "from_section")
            symlinks_sorting_columns.append(c)
        symlinks = SymbolicLink.objects.order_by( *symlinks_sorting_columns ).filter(package__name=pkgname)
        symlinks_count = SymbolicLink.objects.filter(package__name=pkgname).count()
    else:
        symlinks = []
        symlinks_count = 0

    # template rendering time is dominated by the number of links, symlinks have 2 links per row
    if symlinks_count > 125:
        man_pages = paginate(request, "page", man_pages, 250)
        symlinks = paginate(request, "page_symlinks", symlinks, 125)
    else:
        man_pages = paginate(request, "page", man_pages, 500)
        symlinks = paginate(request, "page_symlinks", symlinks, 500)

    context = {
        "url_repo": repo,
        "url_pkgname": pkgname,
        "pkg": db_pkg,
        "man_pages": man_pages,
        "symlinks": symlinks,
    }
    return render(request, "listing.html", context)

def _get_package_filter(repo, pkgname):
    if repo is None and pkgname is None:
        return {}
    elif repo is None:
        return {"package__name": pkgname}
    else:
        return {"package__name": pkgname, "package__repo": repo}

# Maybe all these checks should include repo/pkgname when specified in the URL,
# but this seems enough to parse the URL correctly. debiman actually only checks
# if given section/lang is in some static set.
def _exists_name_section(name, section):
    return ManPage.objects.filter(name=name, section=section).exists() or \
           SymbolicLink.objects.filter(from_name=name, from_section=section).exists()

def _exists_language(lang):
    # cross-language symlinks are not allowed
    return ManPage.objects.filter(lang=lang).exists()

def _exists_name_language(name, lang):
    # cross-language symlinks are not allowed
    return ManPage.objects.filter(name=name, lang=lang).exists()

def _exists_name_section_language(name, section, lang):
    return ManPage.objects.filter(name=name, section=section, lang=lang).exists() or \
           SymbolicLink.objects.filter(from_name=name, from_section=section, lang=lang).exists()

def _parse_man_name_section_lang(url_snippet, *, force_lang=None):
    # Man page names can contain dots, so we need to parse from the right. There are still
    # some ambiguities for shortcuts like gimp-2.8 (shortcut for gimp-2.8(1)), jclient.pl
    # (shortcut for jclient.pl.1.en) etc., but we'll either detect that the page given by
    # the greedy algorithm does not exist or the user can specify the section or language
    # to get the version they want.
    # NOTE: The force_lang parameter can be used to ignore the lang specified in the URL.
    # This is useful for redirections to the default language if we find out that there
    # is no version of the page in the user-specified language.
    parts = url_snippet.split(".")
    if len(parts) == 1:
        # name
        return url_snippet, None, None
    name = ".".join(parts[:-1])
    # the last part can be a section or a language
    if _exists_name_section(name, parts[-1]):
        # any.name.section: language cannot come before section, so we're done
        return name, parts[-1], None
    elif len(parts) == 2:
        if force_lang is not None and not _exists_language(parts[-1]):
            # we still need to validate the input
            return url_snippet, None, None
        if _exists_name_language(name, force_lang or parts[-1]):
            # name.lang
            return name, None, force_lang or parts[-1]
        else:
            # dotted.name
            return url_snippet, None, None
    elif _exists_language(parts[-1]):
        name2 = ".".join(parts[:-2])
        if _exists_name_section_language(name2, parts[-2], force_lang or parts[-1]):
            # name.section.lang
            return name2, parts[-2], force_lang or parts[-1]
        if _exists_name_language(name, force_lang or parts[-1]):
            # name.with.dots.lang
            return name, None, force_lang or parts[-1]
        # name.with.dots
        return url_snippet, None, None
    else:
        # name.with.dots
        return url_snippet, None, None

def try_redirect_or_404(request, repo, pkgname, man_name, man_section, lang, output_type, name_section_lang):
    if man_section is None:
        query = SymbolicLink.objects.filter(from_name=man_name, lang=lang, **_get_package_filter(repo, pkgname))
        # TODO: we're trying to guess the newest version, but lexical ordering is too weak
        query = query.order_by("from_section", "-package__version")[:1]
    else:
        query = SymbolicLink.objects.filter(from_section=man_section, from_name=man_name, lang=lang, **_get_package_filter(repo, pkgname))
        # TODO: we're trying to guess the newest version, but lexical ordering is too weak
        query = query.order_by("-package__version")[:1]

    if len(query) > 0:
        symlink = query[0]
        # repo and pkgname are not added, the target might be in a different package
        url = reverse_man_url("", "", symlink.to_name, symlink.to_section, symlink.lang, output_type)
        return HttpResponseRedirect(url)

    # Try the default language before giving 404.
    # This is important because we don't know if the user explicitly specified
    # the language or followed a link to a localized page, which does not exist.
    # TODO: we could parse the referer header and redirect only links coming from this site
    #
    # Note: if page "foo" does not exist in language "bar", we'll get "foo.bar" as the
    # man_name, so we need to re-parse the URL and force the default language.
    parsed_name, parsed_section, parsed_lang = _parse_man_name_section_lang(name_section_lang, force_lang="en")
    if (parsed_name != man_name or parsed_section != man_section) and parsed_lang == "en":
        url = reverse_man_url(repo, pkgname, parsed_name, parsed_section, "en", output_type)
        return HttpResponseRedirect(url)
    # otherwise page does not exist in en -> 404

    man_page = man_name
    if man_section:
        man_page += "." + man_section

    if repo and pkgname:
        raise Http404("No manual entry for {} found in package {}/{}.".format(man_page, repo, pkgname))
    elif pkgname:
        raise Http404("No manual entry for {} found in package {}.".format(man_page, pkgname))
    else:
        raise Http404("No manual entry for {} found in any package.".format(man_page))

def man_page(request, *, repo=None, pkgname=None, name_section_lang=None, url_output_type=None):
    # validate input parameters
    if repo is not None and pkgname is None:
        return HttpResponse("Specifying repo ({}) without a pkg name should not be allowed.".format(repo), status=500)
    if not name_section_lang:
        return HttpResponse("The name of the man page was not specified.", status=400)
    assert "/" not in name_section_lang
    man_name, man_section, url_lang = _parse_man_name_section_lang(name_section_lang)
    lang = url_lang or "en"
    serve_output_type = url_output_type or "html"
    if serve_output_type not in {"html", "txt", "raw"}:
        return HttpResponse("Serving of {} content type is not implemented yet.".format(serve_output_type), status=501)

    # find the man page and package containing it
    if man_section is None:
        query = ManPage.objects.filter(name=man_name, lang=lang, **_get_package_filter(repo, pkgname))
        # TODO: we're trying to guess the newest version, but lexical ordering is too weak
        query = query.order_by("section", "-package__version")[:1]
    else:
        query = ManPage.objects.filter(section=man_section, name=man_name, lang=lang, **_get_package_filter(repo, pkgname))
        # TODO: we're trying to guess the newest version, but lexical ordering is too weak
        query = query.order_by("-package__version")[:1]

    if len(query) == 0:
        return try_redirect_or_404(request, repo, pkgname, man_name, man_section, lang, url_output_type, name_section_lang)
    else:
        db_man = query[0]
        if man_section is None:
            return HttpResponseRedirect(reverse_man_url(repo, pkgname, man_name, db_man.section, url_lang, url_output_type))
        db_pkg = db_man.package

    if serve_output_type == "raw":
        return HttpResponse(db_man.content.raw, content_type="text/plain; charset=utf8")

    try:
        converted_content = db_man.get_converted(serve_output_type)
    except SoelimError as e:
        raise Http404("The requested manual contains a .so reference to an unknown file. The error is: {}".format(e))

    if serve_output_type == "txt":
        return HttpResponse(converted_content, content_type="text/plain; charset=utf8")

    # links to other packages providing the same manual
    other_packages = []
    query = ManPage.objects.values("package__repo", "package__name") \
                           .filter(section=db_man.section, name=man_name, lang=lang) \
                           .exclude(package__id=db_pkg.id) \
        .union(SymbolicLink.objects.values("package__repo", "package__name") \
                                   .filter(from_section=db_man.section, from_name=man_name, lang=lang) \
                                   .exclude(package__id=db_pkg.id)) \
        .order_by("package__repo", "package__name")
    for row in query:
        info = {
            "repo": row["package__repo"],
            "name": row["package__name"],
        }
        other_packages.append(info)

    # links to other languages - might lead to different package, even if the user specified repo or pkgname
    other_languages = set()
    query = ManPage.objects.values("lang") \
                           .filter(section=db_man.section, name=man_name) \
                           .exclude(lang=lang) \
        .union(SymbolicLink.objects.values("lang") \
                                   .filter(from_section=db_man.section, from_name=man_name) \
                                   .exclude(lang=lang))
    for row in query:
        other_languages.add(row["lang"])

    # links to other sections - might lead to different package, even if the user specified repo or pkgname
    other_sections = set()
    query = ManPage.objects.values("section") \
                           .filter(name=man_name, lang=lang) \
                           .exclude(section=db_man.section) \
        .union(SymbolicLink.objects.values("from_section") \
                                   .filter(from_name=man_name, lang=lang) \
                                   .exclude(from_section=db_man.section))
    for row in query:
        other_sections.add(row["section"])

    # this is pretty fast, no caching
    headings = extract_headings(converted_content)

    context = {
        "lang": lang,  # used in base.html
        "url_repo": repo,
        "url_pkgname": pkgname,
        "url_lang": url_lang,
        "url_output_type": url_output_type,
        "pkg": db_pkg,
        "man": db_man,
        "man_page_content": converted_content,
        "headings": headings,
        "other_packages": other_packages,
        "other_languages": sorted(other_languages),
        "other_sections": sorted(other_sections),
    }

    return render(request, "man_page.html", context)

def build_apropos_filter(q):
    def build_condition(key, value):
        # parse the Django syntax (hardcoded for current models)
        column, operation = key.rsplit("__", maxsplit=1)
        if column.startswith("package__"):
            column = column.split("__", maxsplit=1)[1]
            column = f"\"{package_table}\".\"{column}\""
        else:
            column = f"\"{manpage_table}\".\"{column}\""
        # select the correct operator
        if operation == "exact":
            op = "= %s::text"
        elif operation == "iexact":
            op = "= lower(%s::text)"
            column = f"lower({column})"
        elif operation == "in":
            op = "IN ({})".format(", ".join(["%s::text"] * len(value)))
        elif operation == "startswith":
            op = "~~ %s::text"
            value += "%"
        else:
            raise NotImplementedError(f"Operation {operation} is not implemented for the apropos search.")
        # build the filter condition
        condition = f"{column} {op}"
        return condition, value

    conditions = []
    values = []
    for i in range(len(q.children)):
        if isinstance(q.children[i], Q):
            c, v = build_apropos_filter(q.children[i])
            conditions.append(c)
            values += v
            continue
        key, value = q.children[i]
        condition, value = build_condition(key, value)
        conditions.append(condition)
        if isinstance(value, list):
            values += value
        else:
            values.append(value)

    condition = f" {q.connector} ".join("({})".format(c) for c in conditions)
    return condition, values

# References:
# - https://www.postgresql.org/docs/current/static/pgtrgm.html
# - https://www.postgresql.org/docs/current/static/textsearch.html
# - https://www.postgresql.org/docs/current/static/functions-textsearch.html
# - https://www.postgresql.org/docs/current/static/textsearch-controls.html#textsearch-headline
def search(request):
    search_form = SearchForm(request.GET)
    if not search_form.is_valid():
        return render(request, "search.html", {"search_form": search_form})

    term = search_form.cleaned_data["q"]
    filter_section = search_form.cleaned_data["section"]
    filter_lang = search_form.cleaned_data["lang"]
    filter_repo = search_form.cleaned_data["repo"]
    filter_pkgname = search_form.cleaned_data["pkgname"]

    man_filter = Q()
    pkg_filter = Q()

    if filter_section:
        assert isinstance(filter_section, list)
        man_filter &= reduce(operator.__or__,
                             (Q(section__startswith=q) for q in filter_section))
    if filter_lang:
        assert isinstance(filter_lang, list)
        man_filter &= reduce(operator.__or__,
                             (Q(lang__startswith=q) for q in filter_lang))
    if filter_repo:
        assert isinstance(filter_repo, list)
        man_filter &= Q(package__repo__in=filter_repo)
        pkg_filter &= Q(repo__in=filter_repo)
    if filter_pkgname:
        man_filter &= Q(package__name__iexact=filter_pkgname)
        pkg_filter &= Q(name__iexact=filter_pkgname)

    # this is only because we cannot use .annotate() inside the union (Django would add another column)
    symlink_filter = copy.deepcopy(man_filter)
    def build_symlink_filter(q):
        for i in range(len(q.children)):
            if isinstance(q.children[i], Q):
                build_symlink_filter(q.children[i])
                continue
            key, value = q.children[i]
            if key.startswith("section__"):
                key = "from_" + key
            q.children[i] = (key, value)
    build_symlink_filter(symlink_filter)

    man_results = ManPage.objects.values("name", "section", "lang", "package__repo", "package__name") \
                                 .filter(name__trigram_similar=term).filter(man_filter) \
                                 .annotate(similarity=TrigramSimilarity("name", term)) \
           .union(SymbolicLink.objects.values("from_name", "from_section", "lang", "package__repo", "package__name")
                                      .filter(from_name__trigram_similar=term).filter(symlink_filter)
                                      .annotate(similarity=TrigramSimilarity("from_name", term)),
                  all=True) \
           .order_by("-similarity", "name", "section", "lang")

    # full-text search objects: https://docs.djangoproject.com/en/3.1/ref/contrib/postgres/search/
    ts_query = SearchQuery(term)
    ts_vector = SearchVector("description", config="english")
    ts_headline = SearchHeadline("description", ts_query, start_sel="<b>", stop_sel="</b>")
    #ts_rank = SearchRank(ts_vector, ts_query, normalization=32)
    ts_sim_rank = TrigramSimilarity("name", term) + 2 * SearchRank(ts_vector, ts_query, normalization=32)

    # get table names for the models (needed for raw SQL)
    package_table = Package.objects.model._meta.db_table
    content_table = Content.objects.model._meta.db_table
    manpage_table = ManPage.objects.model._meta.db_table

    # build the WHERE clause (ugh)
    apropos_filter_conditions, apropos_filter_values = build_apropos_filter(man_filter)
    if apropos_filter_conditions:
        apropos_filter = f"WHERE {apropos_filter_conditions}"
    else:
        apropos_filter = ""

    # For the search in man page descriptions ("apropos") we need to perform a raw SQL query,
    # because it is not possible to express the same query with Django ORM.
    # Notes:
    # - the subquery (i.e. INNER JOIN (...) AS subquery) is necessary for good performance
    # - INNER JOIN instead of LEFT OUTER JOIN is needed on the subquery, otherwise PostgreSQL
    #   will not use the GIN index
    # - WITH is used for convenience to avoid repeating the ts_rank expression in the WHERE clause
    #   https://www.postgresql.org/docs/current/queries-with.html
    content_results = f"""
            WITH content_search AS (
                SELECT "{content_table}"."id",
                       ts_headline("{content_table}"."description", plainto_tsquery(%s), 'StartSel=''<b>'', StopSel=''</b>''') AS "desc_snippet",
                       ts_rank(to_tsvector('english'::regconfig, COALESCE("{content_table}"."description", '')), plainto_tsquery(%s), 32) AS "rank",
                       to_tsvector('english'::regconfig, COALESCE("{content_table}"."description", '')) AS "search"
                FROM "{content_table}"
            )
            SELECT *
            FROM "content_search" WHERE "search" @@ plainto_tsquery(%s) AND "rank" > 0.001"""
    apropos_results = ManPage.objects.raw(f"""
            SELECT "{manpage_table}"."id",
                   "{manpage_table}"."name",
                   "{manpage_table}"."section",
                   "{manpage_table}"."lang",
                   "{package_table}"."repo" AS "package__repo",
                   "{package_table}"."name" AS "package__name",
                   "desc_snippet",
                   "rank"
            FROM "{manpage_table}" INNER JOIN "{package_table}" ON ("{manpage_table}"."package_id" = "{package_table}"."id")
                INNER JOIN ({content_results}) AS subquery ON ("{manpage_table}"."converted_content_id" = "subquery"."id")
            {apropos_filter}
            ORDER BY "rank" DESC, "{manpage_table}"."name" ASC, "{manpage_table}"."section" ASC, "{manpage_table}"."lang" ASC, "package__name" ASC, "package__repo" ASC""",
            [term, term, term] + apropos_filter_values)
    # NOTE: Some other things that were tried with Django ORM (as of Django 3.1):
    # 1. We could do this if we did not need a subquery (this works, but is slow):
    #    apropos_results = ManPage.objects.values("name", "section", "lang", "package__repo", "package__name", "converted_content__description").extra(
    #            select={
    #                "desc_snippet": f"ts_headline('english', COALESCE({content_table}.description, ''), plainto_tsquery(%s))",
    #                "rank": f"ts_rank(to_tsvector('english', COALESCE({content_table}.description, '')), plainto_tsquery(%s), 32)",
    #            },
    #            where=[f"to_tsvector('english', COALESCE({content_table}.description, '')) @@ plainto_tsquery(%s)"],
    #            params=[term],
    #            select_params=[term, term],
    #            order_by=("-rank", "name", "section", "lang", "package__name", "package__repo"),
    #        )
    #
    # 2. A mostly equivalent query in pure Django ORM syntax (better parametrization, still no subquery, same performance):
    #    from django.db.models import F
    #    apropos_results = ManPage.objects.values("name", "section", "lang", "package__repo", "package__name", "converted_content__description") \
    #                                 .annotate(description=F("converted_content__description")) \
    #                                 .annotate(desc_snippet=ts_headline) \
    #                                 .annotate(rank=ts_rank) \
    #                                 .annotate(search=ts_vector) \
    #                                 .filter(search=ts_query) \
    #                                 .order_by("-rank", "name", "section", "lang", "package__name", "package__repo")
    # 3. We can define the subquery like this, but the real question is how to use it:
    #    content_results = Content.objects.only("id") \
    #                                 .annotate(desc_snippet=ts_headline) \
    #                                 .annotate(rank=ts_rank) \
    #                                 .annotate(search=ts_vector) \
    #                                 .filter(search=ts_query)
    #    Also note that we can't use the subquery even in the plain-text for a raw SQL query,
    #    because the ".query" attribute strips '' from the COALESCE function. [WTF!!!]
    # 3a) Django supports subqueries like this: https://docs.djangoproject.com/en/3.1/ref/models/expressions/#subquery-expressions
    #           SELECT "post"."id", (
    #               SELECT U0."email"
    #               FROM "comment" U0
    #               WHERE U0."post_id" = ("post"."id")
    #               ORDER BY U0."created_at" DESC LIMIT 1
    #           ) AS "newest_commenter_email" FROM "post"
    #    But this is not applicable here, because the subquery *must* return exactly one column
    #    (otherwise it is an SQL syntax error). Anyway, the code (which does not work) would
    #    be more or less like this:
    #       from django.db.models import OuterRef, Subquery
    #       content_results = Content.objects.only("id") \
    #                                    .annotate(desc_snippet=ts_headline) \
    #                                    .annotate(rank=ts_rank) \
    #                                    .annotate(search=ts_vector) \
    #                                    .filter(Q(search=ts_query) & Q(id=OuterRef("converted_content_id")))    # this is basically the join condition
    #       apropos_results = ManPage.objects.values("name", "section", "lang", "package__repo", "package__name") \
    #                                    .annotate(content_subquery=Subquery(content_results)) \
    #                                    .order_by("-rank", "name", "section", "lang", "package__name", "package__repo")
    # 3b) Django supports joins with simple subqueries via FilteredRelation objects, but it
    #     does not work with arbitrary subqueries, especially subqueries which add additional
    #     columns (like our "desc_snippet" and "rank").
    #     https://docs.djangoproject.com/en/3.1/ref/models/querysets/#filteredrelation-objects

    # Note: the "Q" objects allow more complicated expressions in the filter:
    # https://docs.djangoproject.com/en/3.1/topics/db/queries/#complex-lookups-with-q
    pkg_results = Package.objects.only("repo", "name") \
                                 .annotate(desc_snippet=ts_headline) \
                                 .annotate(rank=ts_sim_rank) \
                                 .annotate(search=ts_vector) \
                                 .filter(pkg_filter) \
                                 .filter(Q(name__trigram_similar=term) | Q(search=ts_query)) \
                                 .order_by("-rank", "name", "repo")

    man_results = paginate(request, "page_man", man_results, 20)
    apropos_results = paginate(request, "page_apropos", apropos_results, 20)
    pkg_results = paginate(request, "page_pkg", pkg_results, 20)

    context = {
        "search_form": search_form,
        "man_results": man_results,
        "apropos_results": apropos_results,
        "pkg_results": pkg_results,
    }

    return render(request, "search.html", context)
