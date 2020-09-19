from django import forms
from django.core.cache import cache

from .models import Package, ManPage

class SearchForm(forms.Form):
    error_css_class = "form-error"
    required_css_class = "form-required"

    q = forms.CharField(label="Keywords", help_text="Enter search keywords")
    section = forms.MultipleChoiceField()
    lang = forms.MultipleChoiceField()
    repo = forms.MultipleChoiceField()
    pkgname = forms.CharField(
                    label="Package name",
                    help_text="Limit results to a specific package name",
                    required=False
                )

    def __init__(self, querydict, *args, **kwargs):
        super().__init__(querydict, *args, **kwargs)

        # cache common database queries: https://docs.djangoproject.com/en/3.1/topics/cache/#the-low-level-cache-api
        manpage_distinct_section = cache.get_or_set("ManPage:section:distinct", ManPage.objects.values_list("section", flat=True).distinct("section").order_by("section"), timeout=600)
        manpage_distinct_lang = cache.get_or_set("ManPage:lang:distinct", ManPage.objects.values_list("lang", flat=True).distinct("lang").order_by("lang"), timeout=600)
        package_distinct_repo = cache.get_or_set("Package:repo:distinct", Package.objects.values_list("repo", flat=True).distinct("repo").order_by("repo"), timeout=600)

        # django does not support dynamic assignments into the field instances,
        # so the whole fields have to be recreated from scratch
        self.fields["section"] = forms.MultipleChoiceField(
                    label="Section",
                    help_text="Limit search results to a specific section of manuals",
                    choices=[(r, r) for r in manpage_distinct_section],
                    required=False,
                )
        self.fields["lang"] = forms.MultipleChoiceField(
                    label="Language",
                    help_text="Limit search results to a specific language of manuals",
                    choices=[(r, r) for r in manpage_distinct_lang],
                    required=False,
                )
        self.fields["repo"] = forms.MultipleChoiceField(
                    label="Repository",
                    help_text="Limit results to a specific package repository",
                    choices=[(r, r) for r in package_distinct_repo],
                    required=False,
                )
