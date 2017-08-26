from django.conf.urls import url

from . import views

urlpatterns = [
    url(r'^$', views.index, name='index'),
    url(r'^listing/((?P<repo>[A-Za-z0-9@._+-]+)/)??((?P<pkgname>[A-Za-z0-9@._+-]+)/)?$', views.listing, name='listing'),
    url(r'^(?P<lang>[A-Za-z0-9._-]+)/man/(?P<path>([A-Za-z0-9@._+-]+/)*)(?P<man_name>[A-Za-z0-9@._+-:\[\]]+)\.(?P<man_section>[A-Za-z0-9]+)\.html$', views.man_page, name='man_page'),
]
