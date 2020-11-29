from django.urls import re_path

from . import views

urlpatterns = [
    re_path(r"^$", views.index, name="index"),
    re_path(r"^listing(/(?P<repo>[A-Za-z0-9@._+\-]+))??(/(?P<pkgname>[A-Za-z0-9@._+\-]+))?/?$", views.listing, name="listing"),
    re_path(r"^man/"
            r"((?P<repo>[A-Za-z0-9@._+\-]+)/)??"
            r"((?P<pkgname>[A-Za-z0-9@._+\-]+)/)?"
            r"(?P<name_section_lang>[A-Za-z0-9@._+\-:\[\]]+?)"
            r"(\.(?P<url_output_type>html|txt|raw))?$",
            views.man_page, name="man_page"),
    re_path(r"^search", views.search, name="search"),
]
