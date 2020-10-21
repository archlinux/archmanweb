from django.urls import include, path

urlpatterns = [
    path("arch/manpages/", include("archmanweb.urls")),
#    path("", include("archmanweb.urls")),
]
