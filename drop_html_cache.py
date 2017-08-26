#! /usr/bin/env python3

import os

import django
from django.db import connection

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mysite.settings")
django.setup()

with connection.cursor() as c:
    c.execute("UPDATE archweb_manpages_manpage SET html = NULL WHERE html IS NOT NULL;")
