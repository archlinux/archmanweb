from django.urls import include, path

urlpatterns = [
    path("arch/manpages/", include("archweb_manpages.urls")),
#    path("", include("archweb_manpages.urls")),
]
