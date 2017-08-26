import subprocess

from django.shortcuts import render
from django.http import HttpResponse, Http404, HttpResponseRedirect
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models import Count
from django.urls import reverse

from .models import Package, ManPage

def index(request):
    count_man_pages = ManPage.objects.count()
    count_all_pkgs = Package.objects.count()
    count_pkgs_with_mans = ManPage.objects.aggregate(Count("package_id", distinct=True))["package_id__count"]
    context = {
        "count_man_pages": count_man_pages,
        "count_pkgs_with_mans": count_pkgs_with_mans,
        "count_pkgs_without_mans": count_all_pkgs - count_pkgs_with_mans,
    }
    return render(request, "index.html", context)

def listing(request, *, repo=None, pkgname=None):
    sorting = request.GET.get("sorting", "alphabetical")
    lang = request.GET.get("lang")
    section = request.GET.get("section")

    if sorting in "alphabetical":
        man_pages = ManPage.objects.order_by("name", "lang", "section")
    elif sorting == "section":
        man_pages = ManPage.objects.order_by("section", "name", "lang")
    elif sorting == "lang":
        man_pages = ManPage.objects.order_by("lang", "name", "section")
    else:
        raise Http404("Unknown sorting parameter: {}".format(sorting))

    db_pkg = None

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

    paginator = Paginator(man_pages, 500)
    page = request.GET.get("page")
    try:
        man_pages = paginator.page(page)
    except PageNotAnInteger:
        # If page is not an integer, deliver the first page.
        man_pages = paginator.page(1)
    except EmptyPage:
        # If page is out of range, deliver the last page.
        man_pages = paginator.page(paginator.num_pages)

    context = {
        "pkg": db_pkg,
        "man_pages": man_pages,
    }
    return render(request, "listing.html", context)

def man_page(request, lang, path, man_name, man_section):
    # In the future we might support even different architectures and versions
    if path:
        _parts = path.strip("/").split("/")
    else:
        _parts = []
    if len(_parts) == 0:
        repo = None
        pkgname = None
    elif len(_parts) == 1:
        repo = None
        pkgname = _parts[0]
    elif len(_parts) == 2:
        repo = _parts[0]
        pkgname = _parts[1]
    else:
        raise Http404("Invalid package path: {}".format(path))

    def fall_back_to_english():
        url = "/en/man/{}{}.{}.html".format(path, man_name, man_section)
        return HttpResponseRedirect(url)

    # find the man page and package containing it
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
        if repo or pkgname:
            raise Http404("Man page {}({}) not found in package {}.".format(man_name, man_section, path))
        else:
            raise Http404("Man page {}({}) not found in any package.".format(man_name, man_section))
    else:
        db_man = query[0]
        db_pkg = db_man.package

    # links to other packages providing the same manual
    other_versions = []
    query = ManPage.objects.filter(section=man_section, name=man_name, lang=lang) \
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

    # links for other languages - they will lead exactly to the same package iff the user specified "path"
    other_languages = set()
    if repo is None and pkgname is None:
        query = ManPage.objects.filter(section=db_man.section,
                                       name=db_man.name) \
                               .exclude(lang=lang)
    elif repo is None:
        query = ManPage.objects.filter(section=db_man.section,
                                       name=db_man.name,
                                       package__name=pkgname) \
                               .exclude(lang=lang)
    else:
        query = ManPage.objects.filter(section=db_man.section,
                                       name=db_man.name,
                                       package__name=pkgname,
                                       package__repo=repo) \
                               .exclude(lang=lang)
    for man in query:
        other_languages.add(man.lang)

    # links to other sections
    other_sections = set()
    query = ManPage.objects.filter(name=db_man.name, lang=lang) \
            .exclude(section=db_man.section)
    for man in query:
        other_sections.add(man.section)

    # convert the man page to HTML if not already done
    url_pattern = reverse("index") + lang + "/man/%N.%S.html"
    if db_man.html is None or db_man.html_url_pattern != url_pattern:
        cmd = "mandoc -T html -O fragment,man={}".format(url_pattern)
        p = subprocess.run(cmd, shell=True, check=True, input=db_man.content, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert p.stdout
        db_man.html = p.stdout
        db_man.url_pattern = url_pattern
        db_man.save()

    context = {
        "url_path": path,
        "pkg": db_pkg,
        "man": db_man,
        "other_versions": other_versions,
        "other_languages": sorted(other_languages),
        "other_sections": sorted(other_sections),
    }

    return render(request, "man_page.html", context)
