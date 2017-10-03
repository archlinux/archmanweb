#! /usr/bin/env python3

import argparse
import os.path
import logging
import datetime
from pathlib import PurePath
import subprocess

import chardet
import pyalpm

from finder import MANDIR, ManPagesFinder

# init django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mysite.settings")
import django
django.setup()
from django.db import connection, transaction
from django.db.models import Count
from archweb_manpages.models import Package, Content, ManPage, SymbolicLink, UpdateLog, SoelimError


logger = logging.getLogger(__name__)


class UnknownManPath(Exception):
    pass


def decode(text, *, encoding_hint=None):
    CHARSETS = ["utf-8", "ascii", "iso-8859-1", "iso-8859-9", "iso-8859-15", "cp1250", "cp1252"]
    if encoding_hint is not None:
        CHARSETS.insert(0, encoding_hint)

    for charset in CHARSETS:
        try:
            return text.decode(charset)
        except UnicodeDecodeError:
            pass
        except LookupError:
            # ignore invalid encoding_hint
            pass

    # fall back to chardet and errors="replace"
    encoding = chardet.detect(text)["encoding"]
    return text.decode(encoding, errors="replace")


def parse_man_path(path):
    pp = PurePath(path)
    man_name = pp.stem
    man_section = pp.suffix[1:]  # strip the dot

    # relative_to can succeed only if path is a subdir of MANDIR
    if not path.startswith(MANDIR):
        raise UnknownManPath
    pp = pp.relative_to(MANDIR)

    if pp.parts[0].startswith("man"):
        man_lang = "en"
    elif len(pp.parts) > 1 and pp.parts[1].startswith("man"):
        man_lang = pp.parts[0]
    else:
        raise UnknownManPath
    return man_name, man_section, man_lang


def update_packages(finder, *, force=False, only_repos=None):
    updated_pkgs = []

    # update packages in the django database
    for db in finder.sync_db.get_syncdbs():
        if only_repos and db.name not in only_repos:
            continue
        logger.info("Updating packages from repository '{}'...".format(db.name))
        for pkg in db.pkgcache:
            result = Package.objects.filter(repo=db.name, name=pkg.name)
            assert len(result) in {0, 1}
            if len(result) == 0:
                db_package = Package()
                db_package.repo = db.name
                db_package.name = pkg.name
                db_package.arch = pkg.arch
                updated_pkgs.append(pkg)
            else:
                db_package = result[0]
                if pyalpm.vercmp(db_package.version, pkg.version) == -1:
                    updated_pkgs.append(pkg)
                elif force is True:
                    updated_pkgs.append(pkg)
                else:
                    # skip void update of db_package
                    continue

            # update volatile fields (this is run iff the pkg was added to updated_pkgs)
            db_package.version = pkg.version
            db_package.description = pkg.desc
            db_package.url = pkg.url
            db_package.build_date = datetime.datetime.fromtimestamp(pkg.builddate, tz=datetime.timezone.utc)
            db_package.licenses = pkg.licenses
            db_package.save()

    # delete old packages from the django database
    for db_package in Package.objects.order_by("repo").order_by("name"):
        if not finder.pkg_exists(db_package.repo, db_package.name):
            Package.objects.filter(repo=db_package.repo, name=db_package.name).delete()

    return updated_pkgs


def update_man_pages(finder, updated_pkgs):
    logger.info("Updating man pages from {} packages...".format(len(updated_pkgs)))
    updated_pages = 0

    for pkg in updated_pkgs:
        db_pkg = Package.objects.filter(repo=pkg.db.name, name=pkg.name)[0]
        files = set(finder.get_man_files(pkg))
        if not files:
            continue

        # the files above include the .gz suffix, we need to collect even the
        # versions that enter the database
        paths = set()

        # insert/update man pages
        for t, v1, v2 in finder.get_man_contents(pkg):
            if t == "file":
                path, content = v1, v2
                # extract info from path, check if it makes sense
                try:
                    man_name, man_section, man_lang = parse_man_path(path)
                except UnknownManPath:
                    logger.warning("Skipping path with unrecognized structure: {}".format(path))
                    continue

                if not man_section:
                    logger.warning("Skipping path with empty section number: {}".format(path))
                    continue

                # extract the encoding hint (see e.g. evim.1.ru.KOI8-R)
                if "." in man_lang:
                    man_lang, encoding_hint = man_lang.split(".", maxsplit=1)
                else:
                    encoding_hint = None

                # decode the content
                content = decode(content, encoding_hint=encoding_hint)
                # django complains, the DBMS would drop it anyway
                content = content.replace("\0", "")

                if not content:
                    logger.warning("Skipping empty man page: {}".format(path))
                    continue

                paths.add(path)
                result = ManPage.objects.filter(package_id=db_pkg.id, path=path)
                assert len(result) in {0, 1}
                if len(result) == 0:
                    # skip man pages with duplicate encoding
                    if ManPage.objects.filter(package_id=db_pkg.id, name=man_name, section=man_section, lang=man_lang).count() > 0:
                        logger.debug("Skipping man page with duplicate encoding: {}".format(path))
                        continue
                    db_content = Content()
                    db_man = ManPage()
                    db_man.package_id = db_pkg.id
                    db_man.path = path
                    db_man.name = man_name
                    db_man.section = man_section
                    db_man.lang = man_lang
                    db_man.content = db_content

                    # validate and save
                    db_man.full_clean()
                    # TODO: this might still fail if there are multiple foo.1 in different directories and same language
                    db_man.save()
                else:
                    db_man = result[0]
                    db_content = db_man.content

                db_content.raw = content
                db_content.html = None
                db_content.txt = None
                db_content.save()

                updated_pages += 1

            elif t == "symlink":
                source, target = v1, v2

                # extract info from source, check if it makes sense
                try:
                    source_name, source_section, source_lang = parse_man_path(source)
                except UnknownManPath:
                    logger.warning("Skipping symlink with unrecognized structure: {}".format(source))
                    continue

                # drop .gz suffix from target
                if target.endswith(".gz"):
                    target = target[:-3]

                if target.startswith("/"):
                    # make target relative to "/"
                    target = target[1:]
                else:
                    # make target full path
                    ppt = PurePath(source).parent / target
                    # normalize to remove any '..'
                    target = os.path.normpath(ppt)

                # extract info from target, check if it makes sense
                try:
                    target_name, target_section, target_lang = parse_man_path(target)
                except UnknownManPath:
                    logger.warning("Skipping symlink with unknown target: {}".format(target))
                    continue

                # drop cross-language symlinks
                if target_lang != source_lang:
                    logger.warning("Skipping cross-language symlink from {} to {}".format(source, target))
                    continue

                # drop useless redirects
                if target_section == source_section and target_name == source_name:
                    logger.warning("Skipping symlink from {} to {} (the base name is the same).".format(source, target))
                    continue

                # drop encoding from the lang (ru.KOI8-R)
                if "." in source_lang:
                    source_lang, _ = source_lang.split(".", maxsplit=1)

                # save into database
                query = SymbolicLink.objects.filter(package_id=db_pkg.id, lang=source_lang, from_section=source_section, from_name=source_name)
                assert len(query) in {0, 1}
                if len(query) == 0:
                    db_link = SymbolicLink()
                    db_link.package_id = db_pkg.id
                    db_link.lang = source_lang
                    db_link.from_section = source_section
                    db_link.from_name = source_name
                else:
                    db_link = query[0]
                db_link.to_section = target_section
                db_link.to_name = target_name

                # validate and save
                db_link.full_clean()
                db_link.save()

            else:
                raise NotImplementedError("Unknown tarball entry type: {}".format(t))

        # delete man pages whose files no longer exist
        for db_man in ManPage.objects.filter(package_id=db_pkg.id):
            if db_man.path not in paths:
                ManPage.objects.filter(package_id=db_pkg.id, path=db_man.path).delete()

    # delete unreferenced rows from Content
    unreferenced = Content.objects.filter(manpage_content__isnull=True).delete()

    return updated_pages


if __name__ == "__main__":
    # init logging
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    formatter = logging.Formatter("{levelname:8} {message}", style="{")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    parser = argparse.ArgumentParser(description="update man pages in the django database")
    parser.add_argument("--force", action="store_true",
                        help="force an import of man pages from all packages, even if they were not updated recently")
    parser.add_argument("--only-repos", action="store", nargs="+", metavar="NAME",
                        help="import packages (and man pages) only from these repositories")
    parser.add_argument("--only-packages", action="store", nargs="+", metavar="NAME",
                        help="import man pages only from these packages")
    parser.add_argument("--cache-dir", action="store", default="./.cache/",
                        help="path to the cache directory (default: %(default)s)")
    parser.add_argument("--keep-tarballs", action="store_true",
                        help="keep downloaded package tarballs in the cache directory")
    args = parser.parse_args()

    start = datetime.datetime.now(tz=datetime.timezone.utc)

    finder = ManPagesFinder(args.cache_dir)
    finder.refresh()

    # everything in a single transaction
    with transaction.atomic():
        updated_pkgs = update_packages(finder, force=args.force, only_repos=args.only_repos)
        if args.only_packages is None:
            count_updated_pages = update_man_pages(finder, updated_pkgs)
        else:
            count_updated_pages = update_man_pages(finder, [p for p in updated_pkgs if p.name in args.only_packages])

    # this is called outside of the transaction, so that the cache can be reused on errors
    if args.keep_tarballs is False:
        finder.clear_pkgcache()

    # update plain-text (convert_txt is fast, but without preprocessor)
    convert_txt_returncode = None
    if os.path.isfile("./convert_txt"):
        _dbs = django.conf.settings.DATABASES["default"]
        cmd = "./convert_txt --target {}@{} --user {} --password {}" \
              .format(_dbs["NAME"], _dbs["HOST"] or "localhost", _dbs["USER"], _dbs["PASSWORD"])
        p = subprocess.run(cmd, shell=True)
        convert_txt_returncode = p.returncode

    # update remaining plain-text which convert_txt could not handle
    # (one transaction per update, otherwise we might hit memory allocation error)
    def worker(man):
        try:
            man.get_converted("txt")
        except SoelimError:
            logger.error("SoelimError while converting {}.{}.{} to txt".format(man.name, man.section, man.lang))
        except subprocess.CalledProcessError as e:
            logger.error("CalledProcessError while converting {}.{}.{} to txt:\nreturncode = {}\nstderr = {}"
                         .format(man.name, man.section, man.lang, e.returncode, e.stderr))
    queryset = ManPage.objects.only("package", "lang", "content_id", "converted_content_id").filter(content__txt=None).iterator()
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        executor.map(worker, queryset)

    # VACUUM cannot run inside a transaction block
    if updated_pkgs or args.only_packages is not None:
        logger.info("Running VACUUM FULL ANALYZE on our tables...")
        for Model in [Package, Content, ManPage, SymbolicLink]:
            table = Model.objects.model._meta.db_table
            logger.info("--> {}".format(table))
            with connection.cursor() as cursor:
                cursor.execute("VACUUM FULL ANALYZE {};".format(table))

    end = datetime.datetime.now(tz=datetime.timezone.utc)

    # log update
    log = UpdateLog()
    log.timestamp = start
    log.duration = end - start
    log.updated_pkgs = len(updated_pkgs)
    log.updated_pages = count_updated_pages
    log.stats_count_man_pages = ManPage.objects.count()
    log.stats_count_symlinks = SymbolicLink.objects.count()
    log.stats_count_all_pkgs = Package.objects.count()
    log.stats_count_pkgs_with_mans = ManPage.objects.aggregate(Count("package_id", distinct=True))["package_id__count"]
    log.convert_txt_returncode = convert_txt_returncode
    log.save()
