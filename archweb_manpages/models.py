from django.db import models
from django.core.exceptions import ValidationError

class Package(models.Model):
    id = models.AutoField(primary_key=True)
    repo = models.TextField()
    name = models.TextField()
    version = models.TextField()
    arch = models.TextField()

    class Meta:
        unique_together = (
            ('name', 'repo'),
        )

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

    def __str__(self):
        return "<SymbolicLink: package={}, lang={}, from_section={}, from_name={}, to_section={}, to_name>" \
               .format(self.package, self.lang, self.from_section, self.from_name, self.to_section, self.to_name)

    def clean(self):
        # either the section or name must be different
        if self.from_section == self.to_section and self.from_name == self.to_name:
            raise ValidationError("Symbolic link cannot be to the same name and section.")
        if "." in self.lang:
            raise ValidationError("Language tag cannot contain dots.")
