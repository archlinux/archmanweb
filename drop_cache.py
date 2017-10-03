#! /usr/bin/env python3

import os

import django
from django.db import connection

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mysite.settings")
django.setup()

with connection.cursor() as c:
    c.execute("UPDATE archweb_manpages_content SET html = NULL WHERE html IS NOT NULL;")
    c.execute("UPDATE archweb_manpages_content SET txt = NULL WHERE txt IS NOT NULL;")
    c.execute("UPDATE archweb_manpages_manpage SET converted_content_id = NULL WHERE converted_content_id IS NOT NULL;")
    c.execute("VACUUM FULL archweb_manpages_content;")
