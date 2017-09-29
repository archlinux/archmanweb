import re
import subprocess
from pathlib import PurePath

from django.db import models
from django.db import connection
from django.contrib.postgres.indexes import GinIndex
from django.core.exceptions import ValidationError

from .utils import reverse_man_url, postprocess

# ref: https://stackoverflow.com/a/44962928/4180822
class TrigramIndex(GinIndex):
    def get_sql_create_template_values(self, model, schema_editor, using):
        fields = [model._meta.get_field(field_name) for field_name, order in self.fields_orders]
        tablespace_sql = schema_editor._get_index_tablespace_sql(model, fields)
        quote_name = schema_editor.quote_name
        columns = [
            ('%s %s' % (quote_name(field.column), order)).strip() + ' gin_trgm_ops'
            for field, (field_name, order) in zip(fields, self.fields_orders)
        ]
        return {
            'table': quote_name(model._meta.db_table),
            'name': quote_name(self.name),
            'columns': ', '.join(columns),
            'using': using,
            'extra': tablespace_sql,
        }

# django does not support functional indexes (indexes on expressions) out of the box,
# otherwise we could use just this:
#     from django.contrib.postgres.search import SearchVector
#     GinIndex(fields=[SearchVector("description", config="english")])
# see https://code.djangoproject.com/ticket/26167
class SearchVectorIndex(GinIndex):
    def __init__(self, config="english", *args, **kwargs):
        self.config = config
        super().__init__(*args, **kwargs)

    def get_sql_create_template_values(self, model, schema_editor, using):
        fields = [model._meta.get_field(field_name) for field_name, order in self.fields_orders]
        tablespace_sql = schema_editor._get_index_tablespace_sql(model, fields)
        quote_name = schema_editor.quote_name
        columns = [
            ("to_tsvector('%s', %s) %s" % (self.config, quote_name(field.column), order)).strip()
            for field, (field_name, order) in zip(fields, self.fields_orders)
        ]
        return {
            'table': quote_name(model._meta.db_table),
            'name': quote_name(self.name),
            'columns': ', '.join(columns),
            'using': using,
            'extra': tablespace_sql,
        }

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

    # TODO: interesting ArrayField (PostgreSQL-only) attributes: licenses

    class Meta:
        unique_together = (
            ('name', 'repo'),
        )
        if connection.vendor == "postgresql":
            indexes = (
                TrigramIndex(fields=["name"]),
                SearchVectorIndex(fields=["description"], config="english"),
            )

    def __str__(self):
        return "<Package: arch={}, repo={}, name={}, version={}>".format(self.arch, self.repo, self.name, self.version)


class SoelimError(Exception):
    pass


class ManPage(models.Model):
    # would be created automatically anyway
    id = models.AutoField(primary_key=True)

    # package containing the man page
    # NOTE: django emulates ON DELETE, it is not added to the SQL
    package = models.ForeignKey(Package, on_delete=models.CASCADE)

    # path of the file relative to /
    path = models.TextField()

    # man page name
    name = models.TextField()

    # section number (remember that there are multi-character sections like 3p, 3am, 3perl, ...)
    section = models.TextField()

    # language tag
    lang = models.TextField(default="en")

    # content of the man page
    content = models.TextField()

    # cached HTML version of the manual
    # (only the <body>, not the whole page served to users)
    content_html = models.TextField(blank=True, null=True)

    # plain-text version of the content - should be always present to make full-text search possible
    content_txt = models.TextField(blank=True, null=True)

    class Meta:
        unique_together = (
            ('package', 'path'),
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
        if connection.vendor == "postgresql":
            indexes = [TrigramIndex(fields=["name"])]

    def clean(self):
        if not self.path:
            raise ValidationError("Path cannot be empty.")
        if not self.name:
            raise ValidationError("Man name cannot be empty.")
        if not self.section:
            raise ValidationError("Man section cannot be empty.")
        if "." in self.section:
            raise ValidationError("Man section cannot contain dots.")
        if "." in self.lang:
            raise ValidationError("Language tag cannot contain dots.")

    def get_preprocessed_content(self, *, lang, package_id):
        # Strip comments, whitespace etc.
        stripped = re.sub(r'^\.\\".*', "", self.content, flags=re.MULTILINE)
        stripped = stripped.strip()

        # Eliminate the '.so' macro
        # Replacing the content instead of doing a HTTP redirect is closer to the
        # intention behind the .so macro, because the old name stays in the URL.
        # TODO: with a better database structure we would not have to duplicate the resulting HTML/plaintext
        # TODO: check that there are no double redirects
        if re.fullmatch(r"^\.so [A-Za-z0-9@._+\-:\[\]\/]+\s*$", stripped):
            path = self.content.split()[1]
            pp = PurePath(path)
            target_name = pp.stem
            target_section = pp.suffix[1:]  # strip the dot

            # There are actually packages redirecting their manuals to other packages,
            # e.g. shorewall6 -> shorewall. The attribution info provided on the page
            # isn't entirely correct, but that's what the authors intended...
            query = ManPage.objects.filter(section=target_section, name=target_name, lang=lang).values("content", "package_id")[:2]
            query = list(query)

            if len(query) == 0:
                raise SoelimError
            elif len(query) == 1:
                return query[0]["content"]

            # if the query is ambiguous, the only thing we can try is to check package_id
            try:
                return ManPage.objects.values_list("content", flat=True).get(section=target_section, name=target_name, lang=lang, package_id=package_id)
            except ManPage.DoesNotExist:
                raise SoelimError

        return self.content

    @staticmethod
    def _convert(content, output_type, lang=None):
        if output_type == "html":
            url_pattern = reverse_man_url("", "", "%N", "%S", lang, "")
            cmd = "mandoc -T html -O fragment,man={}".format(url_pattern)
        elif output_type == "txt":
            cmd = "mandoc -T utf8"
        p = subprocess.run(cmd, shell=True, check=True, input=content, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert p.stdout
        return p.stdout

    def get_converted(self, output_type, lang, package_id):
        assert output_type in {"html", "txt"}
        column = "content_" + output_type

        # convert the man page to HTML/txt if not already done
        if getattr(self, column) is None:
            content = self.get_preprocessed_content(lang=lang, package_id=package_id)
            content = self._convert(content, output_type, lang)
            content = postprocess(content, output_type, lang)
            setattr(self, column, content)
            self.save()

        return getattr(self, column)

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
        if connection.vendor == "postgresql":
            indexes = [TrigramIndex(fields=["from_name"])]

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
