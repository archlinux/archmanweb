import re
import subprocess
from pathlib import PurePath

from django.shortcuts import render
from django.http import HttpResponse, Http404, HttpResponseRedirect
from django.db.models import Count
from django.contrib.postgres.search import TrigramSimilarity

from .models import Package, ManPage, SymbolicLink, UpdateLog
from .utils import reverse_man_url, paginate, postprocess, extract_headings

def index(request):
    count_man_pages = ManPage.objects.count()
    count_symlinks = SymbolicLink.objects.count()
    count_all_pkgs = Package.objects.count()
    count_pkgs_with_mans = ManPage.objects.aggregate(Count("package_id", distinct=True))["package_id__count"]
    last_updates = UpdateLog.objects.order_by("-id")[:10]
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
    man_pages = ManPage.objects.order_by( *sorting_columns ) \
                               .defer("path", "content", "html")

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
    if serve_output_type not in {"html", "raw"}:
        return HttpResponse("Serving of {} content type is not implemented yet.".format(serve_output_type), status=501)

    # find the man page and package containing it
    if man_section is None:
        query = ManPage.objects.filter(name=man_name, lang=lang, **_get_package_filter(repo, pkgname)) \
                               .defer("path", "content")
        # TODO: we're trying to guess the newest version, but lexical ordering is too weak
        query = query.order_by("section", "-package__version")[:1]
    else:
        query = ManPage.objects.filter(section=man_section, name=man_name, lang=lang, **_get_package_filter(repo, pkgname)) \
                               .defer("path", "content")
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
        return HttpResponse(db_man.content, content_type="text/plain")

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

    # convert the man page to HTML if not already done
    if db_man.html is None:
        # eliminate the '.so' macro
        if re.fullmatch(r"^\.so [A-Za-z0-9@._+\-:\[\]\/]+\s*$", db_man.content):
            path = db_man.content.split()[1]
            pp = PurePath(path)
            target_name = pp.stem
            target_section = pp.suffix[1:]  # strip the dot
            # we search only in the same package, otherwise the attribution info provided on the page wouldn't be correct
            query = ManPage.objects.filter(section=target_section, name=target_name, lang=lang, package_id=db_pkg.id)
            if len(query) == 0:
                raise Http404("The requested manual contains a .so reference to a file, "
                              "which was not found in the same package: {}".format(path))
            # replacing the content instead of doing a HTTP redirect is closer to the
            # intention behind the .so macro, because the old name stays in the URL
            # TODO: with a better database structure we would not have to duplicate the resulting HTML
            # TODO: check that there are no double redirects
            content = query[0].content
        else:
            content = db_man.content

        url_pattern = reverse_man_url("", "", "%N", "%S", lang, "")
        cmd = "mandoc -T html -O fragment,man={}".format(url_pattern)
        p = subprocess.run(cmd, shell=True, check=True, input=content, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert p.stdout
        db_man.html = postprocess(p.stdout, lang)
        db_man.save()

    # this is pretty fast, no caching
    headings = extract_headings(db_man.html)

    context = {
        "lang": lang,  # used in base.html
        "url_repo": repo,
        "url_pkgname": pkgname,
        "url_lang": url_lang,
        "url_output_type": url_output_type,
        "pkg": db_pkg,
        "man": db_man,
        "headings": headings,
        "other_packages": other_packages,
        "other_languages": sorted(other_languages),
        "other_sections": sorted(other_sections),
    }

    return render(request, "man_page.html", context)

def search(request):
    term = request.GET["q"]

    man_results = ManPage.objects.values("name", "section", "lang", "package__repo", "package__name") \
                                 .filter(name__trigram_similar=term) \
                                 .annotate(similarity=TrigramSimilarity("name", term)) \
           .union(SymbolicLink.objects.values("from_name", "from_section", "lang", "package__repo", "package__name")
                                      .filter(from_name__trigram_similar=term)
                                      .annotate(similarity=TrigramSimilarity("from_name", term)),
                  all=True) \
           .order_by("-similarity", "name", "section", "lang")
    man_results = paginate(request, "page_man", man_results, 20)

    pkg_results = Package.objects.values("repo", "name", "description") \
                                 .filter(name__trigram_similar=term) \
                                 .annotate(similarity=TrigramSimilarity("name", term)) \
                                 .order_by("-similarity", "name", "repo")
    pkg_results = paginate(request, "page_pkg", pkg_results, 20)

    context = {
        "man_results": man_results,
        "pkg_results": pkg_results,
    }

    return render(request, "search.html", context)
