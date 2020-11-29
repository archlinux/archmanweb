from django.urls import include, path

urlpatterns = [
    path("", include("archmanweb.urls")),
]
