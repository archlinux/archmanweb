import functools
import pyalpm

from django.shortcuts import render
from django.http import HttpResponse, Http404, HttpResponseRedirect

from ..models import ManPage, SymbolicLink, SoelimError
from ..utils import reverse_man_url, search_url, extract_headings

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
    return ManPage.objects.filter(name=name, section__startswith=section).exists() or \
           SymbolicLink.objects.filter(from_name=name, from_section__startswith=section).exists()

def _exists_language(lang):
    # cross-language symlinks are not allowed
    return ManPage.objects.filter(lang=lang).exists()

def _exists_name_language(name, lang):
    # cross-language symlinks are not allowed
    return ManPage.objects.filter(name=name, lang=lang).exists()

def _exists_name_section_language(name, section, lang):
    return ManPage.objects.filter(name=name, section__startswith=section, lang=lang).exists() or \
           SymbolicLink.objects.filter(from_name=name, from_section__startswith=section, lang=lang).exists()

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

def _get_section_key(section):
    # section sorting:
    #   - based on mandoc: 1, 8, 6, 2, 3, 5, 7, 4, 9, 3p
    #   - based on man-db: 1, n, l, 8, 3, 0, 2, 5, 4, 9, 6, 7
    order = ("1", "n", "l", "8", "6", "3", "0", "2", "5", "7", "4", "9")
    # sections in the list are ordered first
    if section in order:
        return (order.index(section), "")
    # sections which start with a letter in the list are sorted next
    # (following the same ordering of the first letter and lexical ordering of the rest)
    if section[0] in order:
        return (order.index(section[0]) + len(order), section[1:])
    # other sections are ordered last (respecting the lexical order wrt each other)
    return (100, section)

def _get_repo_key(repo):
    order = ("core", "extra", "community", "multilib", "testing", "community-testing", "multilib-testing")
    if repo in order:
        return (order.index(repo), "")
    return (len(order), repo)

def _get_pkgver_key(version):
    # arguments of vercmp are swapped to order the highest version first
    key_getter = functools.cmp_to_key(lambda a, b: pyalpm.vercmp(b, a))
    return key_getter(version)

def _get_best_match(query, section="section"):
    # prefetch the package object so that we don't hit the db repeatedly while sorting
    # (we can fetch all matches and do the sorting in Python since there are not many
    # ambiguous cases)
    queryset = query.select_related("package").all()
    if len(queryset) == 0:
        return None

    # sorting for best match: section (custom order), repo (custom order), package version (vercmp)
    def sort_key(man):
        sec_key = _get_section_key(getattr(man, section))
        repo_key = _get_repo_key(man.package.repo)
        pkgver_key = _get_pkgver_key(man.package.version)
        return (sec_key, repo_key, pkgver_key)

    queryset = sorted(queryset, key=sort_key)
    return queryset[0]

def get_symlink(repo, pkgname, man_name, man_section, lang, output_type):
    if man_section is None:
        query = SymbolicLink.objects.filter(from_name=man_name, lang=lang, **_get_package_filter(repo, pkgname))
    else:
        query = SymbolicLink.objects.filter(from_section__startswith=man_section, from_name=man_name, lang=lang, **_get_package_filter(repo, pkgname))
    return _get_best_match(query, "from_section")

def try_redirect(repo, pkgname, man_name, man_section, lang, output_type, name_section_lang):
    symlink = get_symlink(repo, pkgname, man_name, man_section, lang, output_type)
    if symlink is not None:
        # repo and pkgname are not added, the target might be in a different package
        url = reverse_man_url("", "", symlink.to_name, symlink.to_section, symlink.lang, output_type)
        return HttpResponseRedirect(url)

    # Try the default language before using the fallback response.
    # This is important because we don't know if the user explicitly specified
    # the language or followed a link to a localized page, which does not exist.
    #
    # Note: if page "foo" does not exist in language "bar", we'll get "foo.bar" as the
    # man_name, so we need to re-parse the URL and force the default language.
    parsed_name, parsed_section, parsed_lang = _parse_man_name_section_lang(name_section_lang, force_lang="en")
    if (parsed_name != man_name or parsed_section != man_section) and parsed_lang == "en":
        url = reverse_man_url(repo, pkgname, parsed_name, parsed_section, "en", output_type)
        return HttpResponseRedirect(url)

# this is used from the search view to redirect directly to the man page
def quick_search(name_section_lang, *, repo=None, pkgname=None):
    man_name, man_section, url_lang = _parse_man_name_section_lang(name_section_lang)
    lang = url_lang or "en"

    # find the man page and package containing it
    if man_section is None:
        query = ManPage.objects.filter(name=man_name, lang=lang, **_get_package_filter(repo, pkgname))
    else:
        query = ManPage.objects.filter(section__startswith=man_section, name=man_name, lang=lang, **_get_package_filter(repo, pkgname))
    db_man = _get_best_match(query)

    if db_man is None:
        return try_redirect(repo, pkgname, man_name, man_section, lang, "", name_section_lang)
    else:
        url = reverse_man_url(repo, pkgname, man_name, man_section, url_lang, "")
        return HttpResponseRedirect(url)

def render_404(request, repo, pkgname, name_section_lang):
    # use naive splitting for the search URL parameters
    # (_parse_man_name_section_lang leaves everything in the first part when
    # the page does not exist. This is ambiguous since the section and lang
    # may get mixed up, but better than nothing.)
    parts = name_section_lang.rsplit(".", maxsplit=2)
    search_name = parts[0]
    search_section = None
    search_lang = None
    if len(parts) > 1:
        search_section = parts[1]
    if len(parts) > 2:
        search_lang = parts[2]

    context = {
        "repo": repo,
        "pkgname": pkgname,
        "name": name_section_lang,
        "search_url": search_url(search_name, section=search_section, lang=search_lang, repo=repo, pkgname=pkgname),
    }

    response = render(request, "man_404.html", context)
    response.status_code = 404
    return response

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
        return HttpResponse("No data for the {} content type are available.".format(serve_output_type), status=400)

    # find the man page and package containing it
    if man_section is None:
        query = ManPage.objects.filter(name=man_name, lang=lang, **_get_package_filter(repo, pkgname))
    else:
        query = ManPage.objects.filter(section__startswith=man_section, name=man_name, lang=lang, **_get_package_filter(repo, pkgname))
    db_man = _get_best_match(query)

    if db_man is None:
        response = try_redirect(repo, pkgname, man_name, man_section, lang, url_output_type, name_section_lang)
        if response:
            return response
        # page does not exist even in the default language, return a nice 404 page with
        # a link to the search form
        return render_404(request, repo, pkgname, name_section_lang)

    if man_section != db_man.section:
        # try a symlink and check if its section is a better match than the man section
        # (e.g. mailx.1 is a symlink to mail.1, which takes precedence over mailx.1p)
        db_symlink = get_symlink(repo, pkgname, man_name, man_section, lang, url_output_type)
        if db_symlink is not None and _get_section_key(db_symlink.from_section) < _get_section_key(db_man.section):
            # repo and pkgname are not added, the target might be in a different package
            url = reverse_man_url("", "", db_symlink.to_name, db_symlink.to_section, db_symlink.lang, url_output_type)
            return HttpResponseRedirect(url)
        # redirect if man_section is None or just a prefix
        url = reverse_man_url(repo, pkgname, man_name, db_man.section, url_lang, url_output_type)
        return HttpResponseRedirect(url)
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
