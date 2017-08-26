from django.db import models

class Package(models.Model):
    id = models.AutoField(primary_key=True)
    repo = models.TextField()
    name = models.TextField()
    version = models.TextField()
    arch = models.TextField()

    # WTF, django does not support compound primary keys
    class Meta:
        unique_together = (
            ('name', 'repo'),
        )

    def __repr__(self):
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
            ('section', 'name'),
            # we need both orders for alphabetical and section ordering
            ('lang', 'name', 'section'),
            ('lang', 'section', 'name'),
            ('name', 'lang'),
        )
