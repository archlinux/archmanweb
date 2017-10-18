# Arch manual pages

## Dependencies

	pacman -S pyalpm python-chardet python-django python-psycopg2 python-requests

## Installation

1. In the directory `mysite` copy `local_settings.py.example` to `local_settings.py` and edit `DEBUG = True` and the `SECRET_KEY` variable.

2. Configure a connection to a [PostgreSQL](https://wiki.archlinux.org/index.php/PostgreSQL) database
   in the [Django database settings](https://docs.djangoproject.com/en/1.11/ref/settings/#databases)
   in the `mysite/local_settings.py` file.

3. Make migrations.

        ./manage.py makemigrations

3. Migrate changes.

        ./manage.py migrate

4. Start the development web server with `./manage.py runserver`. The site
   should be available at http://localhost:8000, saying that there are 0 man
   pages and 0 packages (because they were not imported yet). The server will
   automatically reload when you make changes to the webapp code or templates.

5. Run the `update.py` script to import some man pages. However, note that the
   full import requires to download about 7.5 GB of packages from a mirror of
   the Arch repos and then the extraction takes about 20-30 minutes. (The volume
   of all man pages is less than 300 MB though.) If you won't need all man pages
   for the development, you can run e.g. `update.py --only-repos core` to import
   only man pages from the core repository (the smallest one, download size is
   about 160 MB) or even `update.py --only-packages coreutils man-pages`.
