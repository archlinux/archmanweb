# Arch manual pages

## Git submodules

Make sure that git submodules are initialized after cloning the repository:

    git submodule update --init --recursive

Or initialize the submodules while cloning:

    git clone --recurse-submodules ssh://git@gitlab.archlinux.org:222/archlinux/archmanweb.git

## Dependencies

    pacman -S pyalpm python-chardet python-django python-django-csp python-psycopg2 python-requests python-xtarfile

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

6. Build the [archlinux-common-style](https://gitlab.archlinux.org/archlinux/archlinux-common-style)
   submodule.

   A SASS compiler is needed. For example, install [sassc](https://archlinux.org/packages/community/x86_64/sassc/)
   and run

        cd archlinux-common-style
        make SASS=sassc

7. Start the development web server with `./manage.py runserver`. The site
   should be available at http://localhost:8000, saying that there are 0 man
   pages and 0 packages (because they were not imported yet). The server will
   automatically reload when you make changes to the webapp code or templates.

8. Run the `update.py` script to import some man pages. However, note that the
   full import requires to download about 7.5 GiB of packages from a mirror of
   the Arch repos and then the extraction takes about 20-30 minutes. (The volume
   of all man pages is less than 300 MiB though.) If you won't need all man pages
   for the development, you can run e.g. `update.py --only-repos core` to import
   only man pages from the core repository (the smallest one, download size is
   about 160 MiB) or even `update.py --only-packages coreutils man-pages`.

## About

This website was created for the [man template](https://wiki.archlinux.org/index.php/Template:Man)
on the Arch wiki. Originally, the template replaced plain text, unclickable
references to man pages with links to [man7.org](https://man7.org/linux/man-pages/),
which contains a handful of manuals taken directly from upstream. Later, we
considered switching to another site providing more manuals. Since we did not
find a suitable external site, we decided to build a new service to satisfy all
our requirements:

1. All man pages from official Arch packages are available. Old versions and
   permalinks are not necessary.
2. Functionality does not require Javascript.
3. Pages are addressable by their name and section, both occurring exactly once
   in the URL to avoid problems with pages such as
   [ar(1)](https://man.archlinux.org/man/ar.1) and
   [ar(1p)](https://man.archlinux.org/man/ar.1p).
4. The URLs used by the _man_ template should not redirect to permalinks,
   otherwise users would start copy-pasting them to the wiki and it would be
   hard to check if they are the same as the canonical URLs.
5. Human-readable subsection anchors.
6. The page should clearly indicate the Arch package version containing the
   page.

See the [original discussion](https://wiki.archlinux.org/index.php/Template_talk:Man#Sources)
for details.

We used a dynamic approach instead of building a website consisting of
completely static pages. The main building blocks are the
[Django web framework](https://www.djangoproject.com/), the
[PostgreSQL](https://www.postgresql.org/) database server, the `mandoc` tool
from the [mandoc toolset](http://mdocml.bsd.lv/) for the conversion to HTML and
the [pyalpm](https://github.com/archlinux/pyalpm) library for data extraction
from the Arch repositories. The code is available in the
[archmanweb](https://gitlab.archlinux.org/archlinux/archmanweb) repository at
GitHub.

Overall, this approach allows us to provide the following features without
rebuilding the whole website from scratch:

- Listings with custom filters and orderings.
- Links to other versions of the same manual provided by different packages.
- Links to similar manuals available in other sections or languages.
- Searching in the names and descriptions of packages and manuals, similarly to
  [apropos(1)](https://man.archlinux.org/man/apropos.1).

### Similar projects

Some similar projects, each using a different approach, are:

- [manned.org](https://manned.org/) ([code](https://g.blicky.net/manned.git/),
  [Arch BBS thread](https://bbs.archlinux.org/viewtopic.php?id=145382))
- [man7.org](http://man7.org/linux/man-pages/) (no idea about website scripts)
- [manpages.debian.org](https://manpages.debian.org/)
  ([source](https://github.com/Debian/debiman/))
- [man.openbsd.org](http://man.openbsd.org/) (runs with the mandoc CGI script)

## Test cases

These links serve as test cases to ensure that all features still work, they
are not useful to regular users.

### URLs with dots

- <a href="https://man.archlinux.org/man/intro">intro</a>
- <a href="https://man.archlinux.org/man/intro.1">intro.1</a>
- <a href="https://man.archlinux.org/man/intro.1.en">intro.1.en</a>
- <a href="https://man.archlinux.org/man/intro.en">intro.en</a>
- <a href="https://man.archlinux.org/man/systemd.service">systemd.service</a>
- <a href="https://man.archlinux.org/man/systemd.service.5">systemd.service.5</a>
- <a href="https://man.archlinux.org/man/systemd.service.5.en">systemd.service.5.en</a>
- <a href="https://man.archlinux.org/man/systemd.service.en">systemd.service.en</a>
- <a href="https://man.archlinux.org/man/gimp-2.8">gimp-2.8</a>
- <a href="https://man.archlinux.org/man/gimp-2.8.1">gimp-2.8.1</a>
- <a href="https://man.archlinux.org/man/gimp-2.8.1.en">gimp-2.8.1.en</a>
- <a href="https://man.archlinux.org/man/gimp-2.8.en">gimp-2.8.en</a>
- <a href="https://man.archlinux.org/man/CA.pl">CA.pl</a>
- <a href="https://man.archlinux.org/man/CA.pl.1ssl">CA.pl.1ssl</a>
- <a href="https://man.archlinux.org/man/CA.pl.1ssl.en">CA.pl.1ssl.en</a>
- <a href="https://man.archlinux.org/man/CA.pl.en">CA.pl.en</a>

### Best match lookup

Ambiguous cases are ordered by section, package repository and package version,
then the first manual is selected.

- <a href="https://man.archlinux.org/man/mount">mount</a> redirects to
  <a href="https://man.archlinux.org/man/mount.8">mount.8</a>
  (not <a href="https://man.archlinux.org/man/mount.2">mount.2</a>)
- <a href="https://man.archlinux.org/man/gv">gv</a> redirects to
  <a href="https://man.archlinux.org/man/gv.1">gv.1</a>
  (not <a href="https://man.archlinux.org/man/gv.3guile">gv.3guile</a>,
  <a href="https://man.archlinux.org/man/gv.3lua">gv.3lua</a> etc.)
- <a href="https://man.archlinux.org/man/graphviz/gv">graphviz/gv</a> redirects to
  <a href="https://man.archlinux.org/man/graphviz/gv.3guile">graphviz/gv.3guile</a>
  (not <a href="https://man.archlinux.org/man/graphviz/gv.3lua">graphviz/gv.3lua</a> etc.)
- <a href="https://man.archlinux.org/man/gv.3">gv.3</a> redirects to
  <a href="https://man.archlinux.org/man/gv.3guile">gv.3guile</a>
  (not <a href="https://man.archlinux.org/man/gv.1">gv.1</a>,
  <a href="https://man.archlinux.org/man/gv.3lua">gv.3lua</a> etc.)
- <a href="https://man.archlinux.org/man/aliases.5">aliases.5</a> displays
  <a href="https://man.archlinux.org/man/extra/postfix/aliases.5">extra/postfix/aliases.5</a>
  (not <a href="https://man.archlinux.org/man/community/opensmtpd/aliases.5">community/opensmtpd/aliases.5</a>)
- <a href="https://man.archlinux.org/man/mysqld.8">mysqld.8</a> displays
  <a href="https://man.archlinux.org/man/extra/mariadb/mysqld.8">extra/mariadb/mysqld.8</a>
  (not <a href="https://man.archlinux.org/man/community/percona-server/mysqld.8">community/percona-server/mysqld.8</a>)
- <a href="https://man.archlinux.org/man/mailx">mailx</a> and
  <a href="https://man.archlinux.org/man/mailx.1">mailx.1</a> redirect to
  <a href="https://man.archlinux.org/man/mail.1.en">mail.1.en</a> as a symbolic link
  (not <a href="https://man.archlinux.org/man/mailx.1p">mailx.1p</a>)

### Language fallback

- <a href="https://man.archlinux.org/man/nvidia-smi.cs">nvidia-smi.cs</a> &rarr;
  <a href="https://man.archlinux.org/man/nvidia-smi.en">nvidia-smi.en</a> &rarr;
  <a href="https://man.archlinux.org/man/nvidia-smi.1.en">nvidia-smi.1.en</a>
  (maybe we should try harder and avoid the double redirect)
- <a href="https://man.archlinux.org/man/nvidia-smi.1.cs">nvidia-smi.1.cs</a> &rarr;
  <a href="https://man.archlinux.org/man/nvidia-smi.1.en">nvidia-smi.1.en</a>
- <a href="https://man.archlinux.org/man/nvidia-smi.foo">nvidia-smi.foo</a> &rarr; 404
- <a href="https://man.archlinux.org/man/nvidia-smi.1.foo">nvidia-smi.1.foo</a> &rarr; 404

### Package filter

- <a href="https://man.archlinux.org/man/nvidia-utils/nvidia-smi.en">nvidia-utils/nvidia-smi.en</a>
- <a href="https://man.archlinux.org/man/nvidia-340xx-utils/nvidia-smi.en">nvidia-340xx-utils/nvidia-smi.en</a>
- <a href="https://man.archlinux.org/man/nvidia-utils/nvidia-smi.cs">nvidia-utils/nvidia-smi.cs</a> &rarr;
  <a href="https://man.archlinux.org/man/nvidia-utils/nvidia-smi.en">nvidia-utils/nvidia-smi.en</a>
- <a href="https://man.archlinux.org/man/nvidia-340xx-utils/nvidia-smi.cs">nvidia-340xx-utils/nvidia-smi.cs</a> &rarr;
  <a href="https://man.archlinux.org/man/nvidia-340xx-utils/nvidia-smi.cs">nvidia-utils/nvidia-340xx-smi.en</a>
- <a href="https://man.archlinux.org/man/foo/nvidia-smi.cs">foo/nvidia-smi.cs</a> &rarr; 404
- <a href="https://man.archlinux.org/man/foo/nvidia-smi.en">foo/nvidia-smi.en</a> &rarr; 404

### .so macros

There is a <a href="https://man.archlinux.org/man/groff.1">groff(1)</a> extension for the
<a href="https://man.archlinux.org/man/man.7">man(7)</a> and
<a href="https://man.archlinux.org/man/mdoc.7">mdoc(7)</a>
languages to include contents of other files using the `.so` macro. In normal
operation where manuals are stored as files on a file system, the
<a href="https://man.archlinux.org/man/soelim.1">soelim(1)</a>
pre-processor handles the inclusion. Our system is based on a database rather
than a file system, so we need a custom `soelim` as well.

Some pages which contain the `.so` macro:

- <a href="https://man.archlinux.org/man/[.1.zh_CN">[.1.zh_CN</a>
- <a href="https://man.archlinux.org/man/pwunconv.8">pwunconv(8)</a>
- <a href="https://man.archlinux.org/man/pam.8">pam(8)</a>
- <a href="https://man.archlinux.org/man/url.7">url(7)</a>
- <a href="https://man.archlinux.org/man/xorg.conf.d.5">xorg.conf.d(5)</a>
- <a href="https://man.archlinux.org/man/glibc.7">glibc(7)</a>
- <a href="https://man.archlinux.org/man/systemd-logind.8">systemd-logind(8)</a>
- <a href="https://man.archlinux.org/man/shorewall6.conf.5">shorewall6.conf(5)</a>
  points to a page contained in a different package (`shorewall` instead of `shorewall6`)
- <a href="https://man.archlinux.org/man/lsof.8">lsof(8)</a>
  (not a "hardlink", includes an invalid file `./00DIALECTS`)
