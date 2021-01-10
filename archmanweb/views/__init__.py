from django.shortcuts import render
from django.http import Http404
from django.db.models import Count

from ..models import Package, ManPage, SymbolicLink, UpdateLog

# make views available from the main "views" package
from .listing import listing
from .man_page import man_page
from .search import search

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
