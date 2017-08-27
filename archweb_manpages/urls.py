from django.conf.urls import url

from . import views

urlpatterns = [
    url(r'^$', views.index, name='index'),
    url(r'^listing/((?P<repo>[A-Za-z0-9@._+\-]+)/)??((?P<pkgname>[A-Za-z0-9@._+\-]+)/)?$', views.listing, name='listing'),
    url(r'^man/'
        r'((?P<repo>[A-Za-z0-9@._+\-]+)/)??'
        r'((?P<pkgname>[A-Za-z0-9@._+\-]+)/)?'
        r'(?P<man_name>[A-Za-z0-9@._+\-:\[\]]+?)'
        r'(\.(?P<section_or_lang>[A-Za-z0-9._\-]+?))??'
        r'(\.(?P<lang_or_section>[A-Za-z0-9._\-]+?))??'
        r'(\.(?P<url_output_type>html|raw))?$',
        views.man_page, name='man_page'),
]
