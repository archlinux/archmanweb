import operator
from functools import reduce

from django.shortcuts import render
from django.http import HttpResponse, Http404
from django import forms
from django.db.models import Q

from ..models import Package, ManPage, SymbolicLink
from ..utils import paginate
from .search import SearchForm

class ListingForm(SearchForm):
    # remove the "q" field
    q = None

    # add sorting fields
    sort_by = forms.ChoiceField(
                label="Sort by",
                help_text="Order the results by the specified field",
                choices=[("name", "Name"), ("section", "Section"), ("lang", "Language")],
                required=False,
            )
    sort_order = forms.ChoiceField(
                label="Sort order",
                help_text="Order the results in the specified order",
                choices=[("asc", "Ascending"), ("desc", "Descending")],
                required=False,
            )

def listing(request, *, repo=None, pkgname=None):
    # move repo and pkgname from the URL path to the query string
    query = request.GET.copy()
    if repo is not None:
        query["repo"] = repo
    if pkgname is not None:
        query["pkgname"] = pkgname

    # init and validate the form
    listing_form = ListingForm(query)
    if not listing_form.is_valid():
        return render(request, "listing.html", {"listing_form": listing_form})

    # get parameters from the form
    repo = listing_form.cleaned_data["repo"]
    pkgname = listing_form.cleaned_data["pkgname"]
    section = listing_form.cleaned_data["section"]
    lang = listing_form.cleaned_data["lang"]
    sort_by = listing_form.cleaned_data["sort_by"] or "name"
    sort_order = listing_form.cleaned_data["sort_order"] or "asc"

    if sort_by == "name":
        sorting_columns = ("name", "lang", "section")
    elif sort_by == "section":
        sorting_columns = ("section", "name", "lang")
    elif sort_by == "lang":
        sorting_columns = ("lang", "name", "section")

    if sort_order == "desc":
        sorting_columns = ("-" + c for c in sorting_columns)

    db_pkg = None
    man_pages = ManPage.objects.order_by( *sorting_columns )

    if pkgname:
        # check that such package exists
        if repo:
            query = Package.objects.filter(name=pkgname, repo__in=repo)
        else:
            query = Package.objects.filter(name=pkgname)
        if len(query) == 0:
            if len(repo) > 1:
                raise Http404("The package {} does not exist in the {} repositories.".format(pkgname, repo))
            elif len(repo) == 1:
                raise Http404("The package {} does not exist in the {} repositoriy.".format(pkgname, repo[0]))
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
    elif repo:
        man_pages = man_pages.filter(package__repo__in=repo)
    if section:
        assert isinstance(section, list)
        section_parts = []
        for q in section:
            # do prefix search only when given a single letter (e.g. "3p" should not match "3perl", "3python" etc.)
            if len(q) == 1:
                section_parts.append(Q(section__startswith=q))
            else:
                section_parts.append(Q(section__iexact=q))
        section_filter = reduce(operator.__or__, section_parts)
        man_pages = man_pages.filter(section_filter)
    if lang:
        assert isinstance(lang, list)
        lang_filter = reduce(operator.__or__,
                             (Q(lang__startswith=q) for q in lang))
        man_pages = man_pages.filter(lang_filter)

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
        "listing_form": listing_form,
        "pkg": db_pkg,
        "man_pages": man_pages,
        "symlinks": symlinks,
    }
    return render(request, "listing.html", context)
