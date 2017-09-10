from django.db import models
from django.db import connection
from django.contrib.postgres.indexes import GinIndex
from django.core.exceptions import ValidationError

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
            indexes = [TrigramIndex(fields=["name"])]

    def __str__(self):
        return "<Package: arch={}, repo={}, name={}, version={}>".format(self.arch, self.repo, self.name, self.version)


class ManPage(models.Model):
    # package containing the man page
    # NOTE: django emulates ON DELETE, it is not added to the SQL
    package = models.ForeignKey(Package, on_delete=models.CASCADE)

    # path of the file relative to /
    path = models.TextField()

    # section number (remember that there are multi-character sections like 3p, 3am, 3perl, ...)
    section = models.TextField()

    # man page name
    name = models.TextField()

    # content of the man page
    content = models.TextField()

    # language tag
    lang = models.TextField(default="en")

    # cached HTML version of the manual
    # (only the <body>, not the whole page served to users)
    html = models.TextField(blank=True, null=True)

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

class SymbolicLink(models.Model):
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
