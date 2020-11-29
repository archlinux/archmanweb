#! /usr/bin/env python3

from django.core.management.base import BaseCommand
from django.db import connection

class Command(BaseCommand):
    help = "Drops cached data from the database"

    def handle(self, *args, **kwargs):
        with connection.cursor() as c:
            c.execute("UPDATE archmanweb_content SET html = NULL WHERE html IS NOT NULL;")
            c.execute("UPDATE archmanweb_content SET txt = NULL WHERE txt IS NOT NULL;")
            c.execute("UPDATE archmanweb_content SET description = NULL WHERE description IS NOT NULL;")
            c.execute("UPDATE archmanweb_manpage SET converted_content_id = NULL WHERE converted_content_id IS NOT NULL;")
            c.execute("VACUUM FULL archmanweb_content;")
