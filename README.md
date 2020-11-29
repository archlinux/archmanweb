# Arch manual pages

## Dependencies

	pacman -S pyalpm python-chardet python-django python-psycopg2 python-requests python-xtarfile

## Installation

1. Copy `local_settings.py.example` to `local_settings.py` and edit `DEBUG = True` and the `SECRET_KEY` variable.

2. Configure a connection to a [PostgreSQL](https://wiki.archlinux.org/index.php/PostgreSQL) database
   in the [Django database settings](https://docs.djangoproject.com/en/3.1/ref/settings/#databases)
   in the `local_settings.py` file.

3. Make sure that the [pg_trgm](https://www.postgresql.org/docs/current/pgtrgm.html)
   extension is [created](https://www.postgresql.org/docs/current/sql-createextension.html)
   in the database. For example:

        psql --username=<username> --dbname=<dbname> --command "create extension if not exists pg_trgm;"

4. Make migrations.

        ./manage.py makemigrations

5. Migrate changes.

        ./manage.py migrate

6. Start the development web server with `./manage.py runserver`. The site
   should be available at http://localhost:8000, saying that there are 0 man
   pages and 0 packages (because they were not imported yet). The server will
   automatically reload when you make changes to the webapp code or templates.

7. Run the `update.py` script to import some man pages. However, note that the
   full import requires to download about 7.5 GiB of packages from a mirror of
   the Arch repos and then the extraction takes about 20-30 minutes. (The volume
   of all man pages is less than 300 MiB though.) If you won't need all man pages
   for the development, you can run e.g. `update.py --only-repos core` to import
   only man pages from the core repository (the smallest one, download size is
   about 160 MiB) or even `update.py --only-packages coreutils man-pages`.
