import copy
import operator
from functools import reduce

from django.shortcuts import render
from django import forms
from django.core.cache import cache
from django.db.models import Q
from django.contrib.postgres.search import TrigramSimilarity, SearchQuery, SearchVector, SearchHeadline, SearchRank

from ..models import Package, Content, ManPage, SymbolicLink
from ..utils import paginate
from .man_page import quick_search

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

    # hidden field for quick search
    go = forms.CharField(widget=forms.HiddenInput(), required=False)

    def __init__(self, querydict, *args, **kwargs):
        super().__init__(querydict, *args, **kwargs)

        # cache common database queries: https://docs.djangoproject.com/en/3.1/topics/cache/#the-low-level-cache-api
        manpage_distinct_section = cache.get_or_set("ManPage:section:distinct", ManPage.objects.values_list("section", flat=True).distinct("section").order_by("section"), timeout=600)
        manpage_distinct_lang = cache.get_or_set("ManPage:lang:distinct", ManPage.objects.values_list("lang", flat=True).distinct("lang").order_by("lang"), timeout=600)
        package_distinct_repo = cache.get_or_set("Package:repo:distinct", Package.objects.values_list("repo", flat=True).distinct("repo").order_by("repo"), timeout=600)

        section_descriptions = {
            "1": "1 - General commands",
            "2": "2 - System calls",
            "3": "3 - Library functions",
            "4": "4 - Device files",
            "5": "5 - File formats",
            "6": "6 - Games",
            "7": "7 - Miscellaneous",
            "8": "8 - Privileged commands",
            "9": "9 - Kernel internals",
        }

        # django does not support dynamic assignments into the field instances,
        # so the whole fields have to be recreated from scratch
        self.fields["section"] = forms.MultipleChoiceField(
                    label="Section",
                    help_text="Limit results to a specific manual section or subsection",
                    choices=[(r, section_descriptions.get(r, r)) for r in manpage_distinct_section],
                    required=False,
                )
        self.fields["lang"] = forms.MultipleChoiceField(
                    label="Language",
                    help_text="Limit results to a specific language",
                    choices=[(r, r) for r in manpage_distinct_lang],
                    required=False,
                )
        self.fields["repo"] = forms.MultipleChoiceField(
                    label="Repository",
                    help_text="Limit results to a specific package repository",
                    choices=[(r, r) for r in package_distinct_repo],
                    required=False,
                )

def build_apropos_filter(q):
    def build_condition(key, value):
        # parse the Django syntax (hardcoded for current models)
        column, operation = key.rsplit("__", maxsplit=1)
        if column.startswith("package__"):
            column = column.split("__", maxsplit=1)[1]
            column = f"\"{Package.objects.model._meta.db_table}\".\"{column}\""
        else:
            column = f"\"{ManPage.objects.model._meta.db_table}\".\"{column}\""
        # select the correct operator
        if operation == "exact":
            op = "= %s::text"
        elif operation == "iexact":
            op = "= lower(%s::text)"
            column = f"lower({column})"
        elif operation == "in":
            op = "IN ({})".format(", ".join(["%s::text"] * len(value)))
        elif operation == "startswith":
            op = "~~ %s::text"
            value += "%"
        else:
            raise NotImplementedError(f"Operation {operation} is not implemented for the apropos search.")
        # build the filter condition
        condition = f"{column} {op}"
        return condition, value

    conditions = []
    values = []
    for i in range(len(q.children)):
        if isinstance(q.children[i], Q):
            c, v = build_apropos_filter(q.children[i])
            conditions.append(c)
            values += v
            continue
        key, value = q.children[i]
        condition, value = build_condition(key, value)
        conditions.append(condition)
        if isinstance(value, list):
            values += value
        else:
            values.append(value)

    condition = f" {q.connector} ".join("({})".format(c) for c in conditions)
    return condition, values

# References:
# - https://www.postgresql.org/docs/current/static/pgtrgm.html
# - https://www.postgresql.org/docs/current/static/textsearch.html
# - https://www.postgresql.org/docs/current/static/functions-textsearch.html
# - https://www.postgresql.org/docs/current/static/textsearch-controls.html#textsearch-headline
def search(request):
    search_form = SearchForm(request.GET)
    if not search_form.is_valid():
        return render(request, "search.html", {"search_form": search_form})

    term = search_form.cleaned_data["q"]
    filter_section = search_form.cleaned_data["section"]
    filter_lang = search_form.cleaned_data["lang"]
    filter_repo = search_form.cleaned_data["repo"]
    filter_pkgname = search_form.cleaned_data["pkgname"]

    # handle quick search
    go = search_form.cleaned_data["go"]
    if term and go == "Go" and len(filter_repo) <= 1:
        name_section_lang = term
        if filter_section:
            name_section_lang += filter_section
        if filter_lang:
            name_section_lang += filter_lang
        response = quick_search(repo=filter_repo[0] if len(filter_repo) == 1 else None,
                                pkgname=filter_pkgname or None,
                                name_section_lang=name_section_lang)
        if response:
            return response

    man_filter = Q()
    pkg_filter = Q()

    if filter_section:
        assert isinstance(filter_section, list)
        section_parts = []
        for q in filter_section:
            # do prefix search only when given a single letter (e.g. "3p" should not match "3perl", "3python" etc.)
            if len(q) == 1:
                section_parts.append(Q(section__startswith=q))
            else:
                section_parts.append(Q(section__iexact=q))
        man_filter &= reduce(operator.__or__, section_parts)
    if filter_lang:
        assert isinstance(filter_lang, list)
        man_filter &= reduce(operator.__or__,
                             (Q(lang__startswith=q) for q in filter_lang))
    if filter_repo:
        assert isinstance(filter_repo, list)
        man_filter &= Q(package__repo__in=filter_repo)
        pkg_filter &= Q(repo__in=filter_repo)
    if filter_pkgname:
        man_filter &= Q(package__name__iexact=filter_pkgname)
        pkg_filter &= Q(name__iexact=filter_pkgname)

    # this is only because we cannot use .annotate() inside the union (Django would add another column)
    symlink_filter = copy.deepcopy(man_filter)
    def build_symlink_filter(q):
        for i in range(len(q.children)):
            if isinstance(q.children[i], Q):
                build_symlink_filter(q.children[i])
                continue
            key, value = q.children[i]
            if key.startswith("section__"):
                key = "from_" + key
            q.children[i] = (key, value)
    build_symlink_filter(symlink_filter)

    man_results = ManPage.objects.values("name", "section", "lang", "package__repo", "package__name") \
                                 .filter(name__trigram_similar=term).filter(man_filter) \
                                 .annotate(similarity=TrigramSimilarity("name", term)) \
           .union(SymbolicLink.objects.values("from_name", "from_section", "lang", "package__repo", "package__name")
                                      .filter(from_name__trigram_similar=term).filter(symlink_filter)
                                      .annotate(similarity=TrigramSimilarity("from_name", term)),
                  all=True) \
           .order_by("-similarity", "name", "section", "lang", "package__name", "package__repo")

    # full-text search objects: https://docs.djangoproject.com/en/3.1/ref/contrib/postgres/search/
    ts_query = SearchQuery(term)
    ts_vector = SearchVector("description", config="english")
    ts_headline = SearchHeadline("description", ts_query, start_sel="<b>", stop_sel="</b>")
    #ts_rank = SearchRank(ts_vector, ts_query, normalization=32)
    ts_sim_rank = TrigramSimilarity("name", term) + 2 * SearchRank(ts_vector, ts_query, normalization=32)

    # get table names for the models (needed for raw SQL)
    package_table = Package.objects.model._meta.db_table
    content_table = Content.objects.model._meta.db_table
    manpage_table = ManPage.objects.model._meta.db_table

    # build the WHERE clause (ugh)
    apropos_filter_conditions, apropos_filter_values = build_apropos_filter(man_filter)
    if apropos_filter_conditions:
        apropos_filter = f"WHERE {apropos_filter_conditions}"
    else:
        apropos_filter = ""

    # For the search in man page descriptions ("apropos") we need to perform a raw SQL query,
    # because it is not possible to express the same query with Django ORM.
    # Notes:
    # - the subquery (i.e. INNER JOIN (...) AS subquery) is necessary for good performance
    # - INNER JOIN instead of LEFT OUTER JOIN is needed on the subquery, otherwise PostgreSQL
    #   will not use the GIN index
    # - WITH is used for convenience to avoid repeating the ts_rank expression in the WHERE clause
    #   https://www.postgresql.org/docs/current/queries-with.html
    content_results = f"""
            WITH content_search AS (
                SELECT "{content_table}"."id",
                       ts_headline("{content_table}"."description", plainto_tsquery(%s), 'StartSel=''<b>'', StopSel=''</b>''') AS "desc_snippet",
                       ts_rank(to_tsvector('english'::regconfig, COALESCE("{content_table}"."description", '')), plainto_tsquery(%s), 32) AS "rank",
                       to_tsvector('english'::regconfig, COALESCE("{content_table}"."description", '')) AS "search"
                FROM "{content_table}"
            )
            SELECT *
            FROM "content_search" WHERE "search" @@ plainto_tsquery(%s) AND "rank" > 0.001"""
    apropos_results = ManPage.objects.raw(f"""
            SELECT "{manpage_table}"."id",
                   "{manpage_table}"."name",
                   "{manpage_table}"."section",
                   "{manpage_table}"."lang",
                   "{package_table}"."repo" AS "package__repo",
                   "{package_table}"."name" AS "package__name",
                   "desc_snippet",
                   "rank"
            FROM "{manpage_table}" INNER JOIN "{package_table}" ON ("{manpage_table}"."package_id" = "{package_table}"."id")
                INNER JOIN ({content_results}) AS subquery ON ("{manpage_table}"."converted_content_id" = "subquery"."id")
            {apropos_filter}
            ORDER BY "rank" DESC, "{manpage_table}"."name" ASC, "{manpage_table}"."section" ASC, "{manpage_table}"."lang" ASC, "package__name" ASC, "package__repo" ASC""",
            [term, term, term] + apropos_filter_values)
    # NOTE: Some other things that were tried with Django ORM (as of Django 3.1):
    # 1. We could do this if we did not need a subquery (this works, but is slow):
    #    apropos_results = ManPage.objects.values("name", "section", "lang", "package__repo", "package__name", "converted_content__description").extra(
    #            select={
    #                "desc_snippet": f"ts_headline('english', COALESCE({content_table}.description, ''), plainto_tsquery(%s))",
    #                "rank": f"ts_rank(to_tsvector('english', COALESCE({content_table}.description, '')), plainto_tsquery(%s), 32)",
    #            },
    #            where=[f"to_tsvector('english', COALESCE({content_table}.description, '')) @@ plainto_tsquery(%s)"],
    #            params=[term],
    #            select_params=[term, term],
    #            order_by=("-rank", "name", "section", "lang", "package__name", "package__repo"),
    #        )
    #
    # 2. A mostly equivalent query in pure Django ORM syntax (better parametrization, still no subquery, same performance):
    #    from django.db.models import F
    #    apropos_results = ManPage.objects.values("name", "section", "lang", "package__repo", "package__name", "converted_content__description") \
    #                                 .annotate(description=F("converted_content__description")) \
    #                                 .annotate(desc_snippet=ts_headline) \
    #                                 .annotate(rank=ts_rank) \
    #                                 .annotate(search=ts_vector) \
    #                                 .filter(search=ts_query) \
    #                                 .order_by("-rank", "name", "section", "lang", "package__name", "package__repo")
    # 3. We can define the subquery like this, but the real question is how to use it:
    #    content_results = Content.objects.only("id") \
    #                                 .annotate(desc_snippet=ts_headline) \
    #                                 .annotate(rank=ts_rank) \
    #                                 .annotate(search=ts_vector) \
    #                                 .filter(search=ts_query)
    #    Also note that we can't use the subquery even in the plain-text for a raw SQL query,
    #    because the ".query" attribute strips '' from the COALESCE function. [WTF!!!]
    # 3a) Django supports subqueries like this: https://docs.djangoproject.com/en/3.1/ref/models/expressions/#subquery-expressions
    #           SELECT "post"."id", (
    #               SELECT U0."email"
    #               FROM "comment" U0
    #               WHERE U0."post_id" = ("post"."id")
    #               ORDER BY U0."created_at" DESC LIMIT 1
    #           ) AS "newest_commenter_email" FROM "post"
    #    But this is not applicable here, because the subquery *must* return exactly one column
    #    (otherwise it is an SQL syntax error). Anyway, the code (which does not work) would
    #    be more or less like this:
    #       from django.db.models import OuterRef, Subquery
    #       content_results = Content.objects.only("id") \
    #                                    .annotate(desc_snippet=ts_headline) \
    #                                    .annotate(rank=ts_rank) \
    #                                    .annotate(search=ts_vector) \
    #                                    .filter(Q(search=ts_query) & Q(id=OuterRef("converted_content_id")))    # this is basically the join condition
    #       apropos_results = ManPage.objects.values("name", "section", "lang", "package__repo", "package__name") \
    #                                    .annotate(content_subquery=Subquery(content_results)) \
    #                                    .order_by("-rank", "name", "section", "lang", "package__name", "package__repo")
    # 3b) Django supports joins with simple subqueries via FilteredRelation objects, but it
    #     does not work with arbitrary subqueries, especially subqueries which add additional
    #     columns (like our "desc_snippet" and "rank").
    #     https://docs.djangoproject.com/en/3.1/ref/models/querysets/#filteredrelation-objects

    # Note: the "Q" objects allow more complicated expressions in the filter:
    # https://docs.djangoproject.com/en/3.1/topics/db/queries/#complex-lookups-with-q
    pkg_results = Package.objects.only("repo", "name") \
                                 .annotate(desc_snippet=ts_headline) \
                                 .annotate(rank=ts_sim_rank) \
                                 .annotate(search=ts_vector) \
                                 .filter(pkg_filter) \
                                 .filter(Q(name__trigram_similar=term) | Q(search=ts_query)) \
                                 .order_by("-rank", "name", "repo")

    man_results = paginate(request, "page_man", man_results, 20)
    apropos_results = paginate(request, "page_apropos", apropos_results, 20)
    pkg_results = paginate(request, "page_pkg", pkg_results, 20)

    context = {
        "search_form": search_form,
        "man_results": man_results,
        "apropos_results": apropos_results,
        "pkg_results": pkg_results,
    }

    return render(request, "search.html", context)
