import subprocess

from django.shortcuts import render
from django.http import HttpResponse, Http404, HttpResponseRedirect
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models import Count

from .models import Package, ManPage, SymbolicLink
from .utils import reverse_man_url

def index(request):
    count_man_pages = ManPage.objects.count()
    count_symlinks = SymbolicLink.objects.count()
    count_all_pkgs = Package.objects.count()
    count_pkgs_with_mans = ManPage.objects.aggregate(Count("package_id", distinct=True))["package_id__count"]
    context = {
        "count_man_pages": count_man_pages,
        "count_symlinks": count_symlinks,
        "count_pkgs_with_mans": count_pkgs_with_mans,
        "count_pkgs_without_mans": count_all_pkgs - count_pkgs_with_mans,
    }
    return render(request, "index.html", context)

def paginate(request, url_param, query, limit):
    paginator = Paginator(query, limit)
    page = request.GET.get(url_param)
    try:
        query = paginator.page(page)
    except PageNotAnInteger:
        # If page is not an integer, deliver the first page.
        query = paginator.page(1)
    except EmptyPage:
        # If page is out of range, deliver the last page.
        query = paginator.page(paginator.num_pages)
    return query

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
                symlinks_sorting_columns.append(c.replace("name", "from_name"))
            elif "section" in c:
                symlinks_sorting_columns.append(c.replace("section", "from_section"))
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

# TODO: This doesn't cover languages with explicit encoding, but only utf8 should be allowed
# anyway, because that's what we serve. Adjust the database and update script as necessary.
def _parse_man_name_section_lang(url_snippet):
    # Man page names can contain dots, so we need to parse from the right. There are still
    # some ambiguities for shortcuts like gimp-2.8 (shortcut for gimp-2.8(1)), jclient.pl
    # (shortcut for jclient.pl.1.en) etc., but we'll either detect that the page given by
    # the greedy algorithm does not exist or the user can specify the section or language
    # to get the version they want.
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
        if _exists_name_language(name, parts[-1]):
            # name.lang
            return name, None, parts[-1]
        else:
            # dotted.name
            return url_snippet, None, None
    elif _exists_language(parts[-1]):
        name2 = ".".join(parts[:-2])
        if _exists_name_section_language(name2, parts[-2], parts[-1]):
            # name.section.lang
            return name2, parts[-2], parts[-1]
        if _exists_name_language(name, parts[-1]):
            # name.with.dots.lang
            return name, None, parts[-1]
        # name.with.dots
        return url_snippet, None, None
    else:
        # name.with.dots
        return url_snippet, None, None

def try_symlink_or_404(request, lang, repo, pkgname, man_name, man_section, output_type):
    if man_section is None:
        if repo is None and pkgname is None:
            query = SymbolicLink.objects.filter(from_name=man_name, lang=lang)
        elif repo is None:
            query = SymbolicLink.objects.filter(from_name=man_name, lang=lang, package__name=pkgname)
        else:
            query = SymbolicLink.objects.filter(from_name=man_name, lang=lang, package__name=pkgname, package__repo=repo)
        # TODO: we're trying to guess the newest version, but lexical ordering is too weak
        # TODO: add LIMIT 1
        query = query.order_by("from_section", "-package__version")
    else:
        if repo is None and pkgname is None:
            query = SymbolicLink.objects.filter(from_section=man_section, from_name=man_name, lang=lang)
        elif repo is None:
            query = SymbolicLink.objects.filter(from_section=man_section, from_name=man_name, lang=lang, package__name=pkgname)
        else:
            query = SymbolicLink.objects.filter(from_section=man_section, from_name=man_name, lang=lang, package__name=pkgname, package__repo=repo)
        # TODO: we're trying to guess the newest version, but lexical ordering is too weak
        # TODO: add LIMIT 1
        query = query.order_by("-package__version")

    if len(query) > 0:
        symlink = query[0]
        # repo and pkgname are not added, the target might be in a different package
        url = reverse_man_url("", "", symlink.to_name, symlink.to_section, symlink.lang, output_type)
        return HttpResponseRedirect(url)

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
    if serve_output_type != "html":
        return HttpResponse("Serving of {} content type is not implemented yet.".format(serve_output_type), status=501)

    # this is important because we don't know if the user explicitly specified
    # the language or followed a link to a localized page, which does not exist
    def fall_back_to_english():
        url = reverse_man_url(repo, pkgname, man_name, man_section, "en", url_output_type)
        return HttpResponseRedirect(url)

    # find the man page and package containing it
    if man_section is None:
        if repo is None and pkgname is None:
            query = ManPage.objects.filter(name=man_name, lang=lang)
            if len(query) == 0 and lang != "en":
                return fall_back_to_english()
        elif repo is None:
            query = ManPage.objects.filter(name=man_name, lang=lang, package__name=pkgname)
            if len(query) == 0 and lang != "en":
                return fall_back_to_english()
        else:
            query = ManPage.objects.filter(name=man_name, lang=lang, package__name=pkgname, package__repo=repo)
            if len(query) == 0 and lang != "en":
                return fall_back_to_english()
        # TODO: we're trying to guess the newest version, but lexical ordering is too weak
        query = query.order_by("section", "-package__version")
    else:
        if repo is None and pkgname is None:
            query = ManPage.objects.filter(section=man_section, name=man_name, lang=lang)
            if len(query) == 0 and lang != "en":
                return fall_back_to_english()
        elif repo is None:
            query = ManPage.objects.filter(section=man_section, name=man_name, lang=lang, package__name=pkgname)
            if len(query) == 0 and lang != "en":
                return fall_back_to_english()
        else:
            query = ManPage.objects.filter(section=man_section, name=man_name, lang=lang, package__name=pkgname, package__repo=repo)
            if len(query) == 0 and lang != "en":
                return fall_back_to_english()
        # TODO: we're trying to guess the newest version, but lexical ordering is too weak
        query = query.order_by("-package__version")

    if len(query) == 0:
        return try_symlink_or_404(request, lang, repo, pkgname, man_name, man_section, url_output_type)
    else:
        db_man = query[0]
        if man_section is None:
            return HttpResponseRedirect(reverse_man_url(repo, pkgname, man_name, db_man.section, url_lang, url_output_type))
        db_pkg = db_man.package

    # links to other packages providing the same manual
    other_versions = []
    query = ManPage.objects.filter(section=db_man.section, name=man_name, lang=lang) \
            .exclude(package__id=db_pkg.id) \
            .order_by("package__repo", "package__name")
    for man in query:
        pkg = man.package
        info = {
            "repo": pkg.repo,
            "pkgname": pkg.name,
            "manname": man.name,
            "mansection": man.section,
        }
        other_versions.append(info)

    # links to other languages - might lead to different package, even if the user specified repo or pkgname
    other_languages = set()
    query = ManPage.objects.filter(section=db_man.section,
                                   name=db_man.name) \
                           .exclude(lang=lang)
    for man in query:
        other_languages.add(man.lang)

    # links to other sections - might lead to different package, even if the user specified repo or pkgname
    other_sections = set()
    query = ManPage.objects.filter(name=db_man.name, lang=lang) \
            .exclude(section=db_man.section)
    for man in query:
        other_sections.add(man.section)

    # convert the man page to HTML if not already done
    if db_man.html is None:
        url_pattern = reverse_man_url("", "", "%N", "%S", lang, "")
        cmd = "mandoc -T html -O fragment,man={}".format(url_pattern)
        p = subprocess.run(cmd, shell=True, check=True, input=db_man.content, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert p.stdout
        db_man.html = p.stdout
        db_man.save()

    context = {
        "url_repo": repo,
        "url_pkgname": pkgname,
        "url_lang": url_lang,
        "url_output_type": url_output_type,
        "pkg": db_pkg,
        "man": db_man,
        "other_versions": other_versions,
        "other_languages": sorted(other_languages),
        "other_sections": sorted(other_sections),
    }

    return render(request, "man_page.html", context)
