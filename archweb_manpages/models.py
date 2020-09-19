import re
from pathlib import PurePath

from django.db import models
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError

from .utils import reverse_man_url, mandoc_convert, postprocess, extract_description

# django does not support functional indexes (indexes on expressions) out of the box,
# otherwise we could use just this:
#     from django.contrib.postgres.search import SearchVector
#     GinIndex(fields=[SearchVector("description", config="english")])
# see https://code.djangoproject.com/ticket/26167
# this is a hack inspired by this blog post:
# https://vxlabs.com/2018/01/31/creating-a-django-migration-for-a-gist-gin-index-with-a-special-index-operator/
class SearchVectorIndex(GinIndex):
    def __init__(self, config="english", *args, **kwargs):
        self.config = config
        super().__init__(*args, **kwargs)

    def create_sql(self, model, schema_editor, **kwargs):
        statement = super().create_sql(model, schema_editor, **kwargs)
        # this works only for one column, otherwise we get a list inside to_tsvector
        # note: coalesce is used because Django uses it even for SearchVector on one column
        statement.template = "CREATE INDEX %(name)s ON %(table)s%(using)s (to_tsvector('" + self.config + "'::regconfig, COALESCE(%(columns)s, '')))%(extra)s"
        return statement

class Package(models.Model):
    id = models.AutoField(primary_key=True)
    repo = models.TextField()
    name = models.TextField()
    version = models.TextField()
    arch = models.TextField()

    # non-essential attributes (useful for search etc.)
    description = models.TextField()
    url = models.TextField(null=True)  # nullable in pacman
    build_date = models.DateTimeField()
    licenses = ArrayField(models.TextField())

    class Meta:
        unique_together = (
            ('name', 'repo'),
        )
        indexes = (
            GinIndex(name="package_name", fields=["name"], opclasses=["gin_trgm_ops"]),
            SearchVectorIndex(name="package_description_search", fields=["description"], config="english"),
        )

    def __str__(self):
        return "<Package: arch={}, repo={}, name={}, version={}>".format(self.arch, self.repo, self.name, self.version)


class SoelimError(Exception):
    pass


class Content(models.Model):
    id = models.AutoField(primary_key=True)

    # raw content of the man page
    raw = models.TextField()

    # cached HTML version of the manual
    # (only the <body>, not the whole page served to users)
    html = models.TextField(blank=True, null=True)

    # plain-text version of the content - should be always present to make full-text search possible
    txt = models.TextField(blank=True, null=True)

    # short plain-text description for full-text ("apropos-like") search
    description = models.TextField(blank=True, null=True)

    class Meta:
        indexes = (
            SearchVectorIndex(name="content_description_search", fields=["description"], config="english"),
        )


class ManPage(models.Model):
    # would be created automatically anyway
    id = models.AutoField(primary_key=True)

    # package containing the man page
    # NOTE: django emulates ON DELETE, it is not added to the SQL
    package = models.ForeignKey(Package, on_delete=models.CASCADE)

    # man page name
    name = models.TextField()

    # section number (remember that there are multi-character sections like 3p, 3am, 3perl, ...)
    section = models.TextField()

    # language tag
    lang = models.TextField(default="en")

    # the original content of this manual
    content = models.ForeignKey(Content, on_delete=models.DO_NOTHING, related_name="manpage_content")

    # shortcut for "hardlinks" due to the .so macro
    # (this significantly reduces storage due to avoiding duplicate HTML and txt)
    converted_content = models.ForeignKey(Content, on_delete=models.SET_NULL, blank=True, null=True, related_name="manpage_converted_content")

    class Meta:
        unique_together = (
            ('package', 'section', 'name', 'lang'),
        )
        index_together = (
            # we need all orders for the listings' ordering
            ('name', 'lang', 'section'),
            ('section', 'name', 'lang'),
            ('lang', 'name', 'section'),
            # for optional 'language' and for filter in 'links to other sections'
            ('section', 'name'),
            # for optional 'section' and for filter in 'links to other sections'
            ('name', 'lang'),
        )
        indexes = [GinIndex(name="manpage_name", fields=["name"], opclasses=["gin_trgm_ops"])]

    def clean(self):
        if not self.name:
            raise ValidationError("Man name cannot be empty.")
        if not self.section:
            raise ValidationError("Man section cannot be empty.")
        if "." in self.section:
            raise ValidationError("Man section cannot contain dots.")
        if "." in self.lang:
            raise ValidationError("Language tag cannot contain dots.")

    # this should always be used instead of `self.content.<format>` to load only
    # the specified field (django does not support auto-defer fields)
    def get_content(self, format, from_converted=None):
        assert hasattr(Content, format)
        if from_converted is None:
            from_converted = format != "raw"
        if from_converted is True:
            content_id = self.converted_content_id
        else:
            content_id = self.content_id
        return Content.objects.values_list(format, flat=True).get(id=content_id)

    def set_content(self, format, text):
        assert hasattr(Content, format)
        assert format != "raw",  "the raw content should not be set with set_content"
        Content.objects.filter(id=self.converted_content_id).update(**{format: text})

    def resolve_so_link(self):
        """
        Detects if the manual is nothing but a "hardlink" to some different page
        using the .so macro.

        Effects:
            - updates self.converted_content_id as necessary
            - raises SoelimError if there is a .so macro which could not be resolved
        """
        if self.converted_content_id is None:
            self.converted_content_id = self.content_id
        else:
            return

        # strip comments, whitespace etc.
        stripped = re.sub(r'^\.\\".*', "", self.get_content("raw"), flags=re.MULTILINE)
        stripped = stripped.strip()

        # eliminate the '.so' macro
        if re.fullmatch(r"^\.so [A-Za-z0-9@._+\-:\[\]\/]+\s*$", stripped):
            path = stripped.split()[1]
            pp = PurePath(path)
            target_name = pp.stem
            target_section = pp.suffix[1:]  # strip the dot

            # There are actually packages redirecting their manuals to other packages,
            # e.g. shorewall6 -> shorewall. The attribution info provided on the page
            # isn't entirely correct, but that's what the authors intended...
            query = ManPage.objects.filter(section=target_section, name=target_name, lang=self.lang).values("content_id", "package_id")[:2]
            query = list(query)

            if len(query) == 0:
                raise SoelimError("unknown target page: {}".format(stripped.split()[1]))
            elif len(query) == 1:
                self.converted_content_id = query[0]["content_id"]
            else:
                # if the query is ambiguous, the only thing we can try is to check package_id
                try:
                    cid = ManPage.objects.values_list("content_id", flat=True) \
                                         .get(section=target_section, name=target_name, lang=self.lang, package_id=self.package_id)
                except ManPage.DoesNotExist:
                    raise SoelimError("ambiguous target page: {}".format(stripped.split()[1]))
                self.converted_content_id = cid

        # save changes to converted_content_id
        self.save()

    def get_preprocessed_content(self, *, visited_ids=None, level=0):
        """
        Performs a recursive elimination of the .so macro and returns the final
        content.

        Effects:
            - calls self.resolve_so_link()
            - raises SoelimError if there is a .so macro pointing to an unknown
              page, there is an inclusion cycle or the recursion depth limit
              has been exceeded
        """
        if visited_ids is None:
            visited_ids = {self.id}
        else:
            if self.id in visited_ids:
                raise SoelimError("inclusion cycle detected")
            elif level > 100:
                raise SoelimError("recursion depth exceeded")

        # resolve "hardlinks" using the .so macro
        self.resolve_so_link()

        # always take from converted, even "hardlinks" may be included in other pages
        content = self.get_content("raw", from_converted=True)

        def repl(match):
            target = match.group("target")
            pp = PurePath(target)
            target_name = pp.stem
            target_section = pp.suffix[1:]  # strip the dot

            # mandoc uses this fallback for invalid references
            fallback = "See the file {}.".format(target)

            # There are actually packages redirecting their manuals to other packages,
            # e.g. shorewall6 -> shorewall. The attribution info provided on the page
            # isn't entirely correct, but that's what the authors intended...
            mans_count = ManPage.objects.filter(section=target_section, name=target_name, lang=self.lang).count()

            if mans_count == 0:
                return fallback
            elif mans_count == 1:
                man = ManPage.objects.get(section=target_section, name=target_name, lang=self.lang)
            else:
                # if the query is ambiguous, the only thing we can try is to check package_id
                try:
                    man = ManPage.objects.get(section=target_section, name=target_name, lang=self.lang, package_id=self.package_id)
                except ManPage.DoesNotExist:
                    return fallback

            return man.get_preprocessed_content(visited_ids=visited_ids | {self.id}, level=level + 1)

        # resolve the remaining .so file inclusions, apply mandoc-style fallback
        content = re.sub(r"^\.so (?P<target>[A-Za-z0-9@._+\-:\[\]\/]+)\s*$", repl, content, flags=re.MULTILINE)
        return content

    def get_converted(self, output_type):
        assert output_type in {"html", "txt"}

        self.resolve_so_link()

        # convert the man page to HTML/txt if not already done
        content = self.get_content(output_type)
        if content is None:
            content = self.get_preprocessed_content()
            content = mandoc_convert(content, output_type, self.lang)
            content = postprocess(content, output_type, self.lang)
            self.set_content(output_type, content)

            if output_type == "txt":
                # update plain-text description
                description = extract_description(content, self.lang)
                Content.objects.filter(id=self.converted_content_id).update(description=description)

        return content


class SymbolicLink(models.Model):
    # would be created automatically anyway
    id = models.AutoField(primary_key=True)

    # package containing the symlink
    # NOTE: django emulates ON DELETE, it is not added to the SQL
    package = models.ForeignKey(Package, on_delete=models.CASCADE)

    # language tag (same for the source and target)
    lang = models.TextField(default="en")

    # source section number
    from_section = models.TextField()

    # source man page name
    from_name = models.TextField()

    # target section number
    to_section = models.TextField()

    # target man page name
    to_name = models.TextField()

    class Meta:
        unique_together = (
            ('package', 'lang', 'from_section', 'from_name'),
        )
        index_together = (
            # for checks in _parse_man_name_section_lang
            ('from_section', 'from_name'),
            ('from_section', 'from_name', 'lang'),
            # for checks in try_symlink_or_404
            ('from_name', 'lang'),
        )
        indexes = [GinIndex(name="symboliclink_from_name", fields=["from_name"], opclasses=["gin_trgm_ops"])]

    def __str__(self):
        return "<SymbolicLink: package={}, lang={}, from_section={}, from_name={}, to_section={}, to_name>" \
               .format(self.package, self.lang, self.from_section, self.from_name, self.to_section, self.to_name)

    def clean(self):
        # either the section or name must be different
        if self.from_section == self.to_section and self.from_name == self.to_name:
            raise ValidationError("Symbolic link cannot be to the same name and section.")
        if "." in self.lang:
            raise ValidationError("Language tag cannot contain dots.")

class UpdateLog(models.Model):
    id = models.AutoField(primary_key=True)

    timestamp = models.DateTimeField()
    duration = models.DurationField()
    updated_pkgs = models.IntegerField()
    updated_pages = models.IntegerField()

    # record also history of statistics after each update
    stats_count_man_pages = models.IntegerField()
    stats_count_symlinks = models.IntegerField()
    stats_count_all_pkgs = models.IntegerField()
    stats_count_pkgs_with_mans = models.IntegerField()

    # return code of the convert_txt program
    convert_txt_returncode = models.IntegerField(null=True)

    class HtmlTableConfig:
        columns = (
            "timestamp",
            "duration",
            "updated_pkgs",
            "updated_pages",
        )
        descriptions = (
            "Time (UTC)",
            "Duration",
            "Updated packages",
            "Updated man pages",
        )
