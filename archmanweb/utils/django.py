from django.urls import reverse
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

__all__ = ["reverse_man_url", "search_url", "paginate"]

def reverse_man_url(repo, pkgname, man_name, man_section, man_lang, content_type):
    # django's reverse function can't reverse our regexes, so we're doing it the old way
    url = reverse("index") + "man/"
    if repo:
        url += repo + "/"
    if pkgname:
        url += pkgname + "/"
    url += man_name
    if man_section:
        url += "." + man_section
    if man_lang:
        url += "." + man_lang
    if content_type:
        url += "." + content_type
    return url

def search_url(man_page, *, section=None, lang=None, repo=None, pkgname=None):
    url = reverse("search")
    url += "?q=" + man_page
    if section:
        url += "&section=" + section
    if lang:
        url += "&lang=" + lang
    if repo:
        url += "&repo=" + repo
    if pkgname:
        url += "&pkgname=" + pkgname
    return url

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
