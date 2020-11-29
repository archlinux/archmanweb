#! /usr/bin/env python3

import argparse
import os.path
import logging
import datetime
from pathlib import PurePath
import subprocess

import chardet
import pyalpm

from archmanweb.management.utils.finder import MANDIR, ManPagesFinder

from django.core.management.base import BaseCommand
import django
from django.db import connection, transaction
from django.db.models import Count
from archmanweb.models import Package, Content, ManPage, SymbolicLink, UpdateLog, SoelimError


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

    if not man_section:
        raise UnknownManPath("empty section number")

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
            try:
                db_package = Package.objects.get(repo=db.name, name=pkg.name)
                if pyalpm.vercmp(db_package.version, pkg.version) == -1:
                    updated_pkgs.append(pkg)
                elif force is True:
                    updated_pkgs.append(pkg)
                else:
                    # skip void update of db_package
                    continue
            except Package.DoesNotExist:
                db_package = Package()
                db_package.repo = db.name
                db_package.name = pkg.name
                db_package.arch = pkg.arch
                updated_pkgs.append(pkg)

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

        # set of unique keys (tuples) of pages present in the package,
        # the rest will be deleted from the database
        keys = set()

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

                if (man_name, man_section, man_lang) in keys:
                    logger.debug("Skipping duplicate man page (maybe duplicate encoding): {}".format(path))
                    continue
                keys.add( (man_name, man_section, man_lang) )

                # find or create Content instance
                try:
                    db_man = ManPage.objects.get(package_id=db_pkg.id, name=man_name, section=man_section, lang=man_lang)
                    db_content = db_man.content
                except ManPage.DoesNotExist:
                    db_man = None
                    db_content = Content()

                # update content
                db_content.raw = content
                db_content.html = None
                db_content.txt = None
                db_content.save()

                # update newly-created ManPage instance
                if db_man is None:
                    db_man = ManPage()
                    db_man.package_id = db_pkg.id
                    db_man.name = man_name
                    db_man.section = man_section
                    db_man.lang = man_lang
                    db_man.content = db_content

                    # db_man has to be saved after db_content, because django's
                    # validation is not deferrable (and db_content.id is not
                    # known until the content is saved)
                    db_man.full_clean()
                    # TODO: this might still fail if there are multiple foo.1 in different directories and same language
                    db_man.save()

                updated_pages += 1

            elif t == "hardlink":
                # hardlinks can't point to non-existent files, so they can be stored in the ManPage table
                source, target = v1, v2

                # extract info from source, check if it makes sense
                try:
                    source_name, source_section, source_lang = parse_man_path(source)
                except UnknownManPath:
                    logger.warning("Skipping hardlink with unrecognized source path: {}".format(source))
                    continue

                # extract info from target, check if it makes sense
                try:
                    target_name, target_section, target_lang = parse_man_path(target)
                except UnknownManPath:
                    logger.warning("Skipping hardlink with unrecognized target path: {}".format(target))
                    continue

                # drop encoding from the lang (ru.KOI8-R)
                if "." in source_lang:
                    source_lang, _ = source_lang.split(".", maxsplit=1)
                if "." in target_lang:
                    target_lang, _ = target_lang.split(".", maxsplit=1)

                # drop useless redirects
                if target_lang == source_lang and target_section == source_section and target_name == source_name:
                    logger.warning("Skipping hardlink from {} to {} (the base name is the same).".format(source, target))
                    continue

                if (source_name, source_section, source_lang) in keys:
                    logger.debug("Skipping duplicate hardlink: {}".format(source))
                    continue
                keys.add( (source_name, source_section, source_lang) )

                # save into database
                man_target = ManPage.objects.get(package_id=db_pkg.id, name=target_name, section=target_section, lang=target_lang)
                try:
                    man_source = ManPage.objects.get(package_id=db_pkg.id, name=source_name, section=source_section, lang=source_lang)
                except ManPage.DoesNotExist:
                    man_source = ManPage(
                        package_id=db_pkg.id,
                        name=source_name,
                        section=source_section,
                        lang=source_lang
                    )
                man_source.content_id = man_target.content_id

                # validate and save
                man_source.full_clean()
                man_source.save()

                updated_pages += 1

            elif t == "symlink":
                source, target = v1, v2

                # extract info from source, check if it makes sense
                try:
                    source_name, source_section, source_lang = parse_man_path(source)
                except UnknownManPath:
                    logger.warning("Skipping symlink with unrecognized structure: {}".format(source))
                    continue

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

                # drop encoding from the lang (ru.KOI8-R)
                if "." in source_lang:
                    source_lang, _ = source_lang.split(".", maxsplit=1)
                if "." in target_lang:
                    target_lang, _ = target_lang.split(".", maxsplit=1)

                # drop cross-language symlinks
                if target_lang != source_lang:
                    logger.warning("Skipping cross-language symlink from {} to {}".format(source, target))
                    continue

                # drop useless redirects
                if target_section == source_section and target_name == source_name:
                    logger.warning("Skipping symlink from {} to {} (the base name is the same).".format(source, target))
                    continue

                # save into database
                try:
                    db_link = SymbolicLink.objects.get(package_id=db_pkg.id, lang=source_lang, from_section=source_section, from_name=source_name)
                except SymbolicLink.DoesNotExist:
                    db_link = SymbolicLink(
                        package_id=db_pkg.id,
                        lang=source_lang,
                        from_section=source_section,
                        from_name=source_name,
                    )
                db_link.to_section = target_section
                db_link.to_name = target_name

                # validate and save
                db_link.full_clean()
                db_link.save()

            else:
                raise NotImplementedError("Unknown tarball entry type: {}".format(t))

        # delete man pages whose files no longer exist
        for db_man in ManPage.objects.filter(package_id=db_pkg.id):
            if (db_man.name, db_man.section, db_man.lang) not in keys:
                ManPage.objects.filter(package_id=db_pkg.id, name=db_man.name, section=db_man.section, lang=db_man.lang).delete()

    # delete unreferenced rows from Content
    unreferenced = Content.objects.filter(manpage_content__isnull=True).delete()

    return updated_pages


class Command(BaseCommand):
    help = "Update man pages in the Django database"

    def __init__(self, *args, **kwargs):
        BaseCommand.__init__(self, *args, **kwargs)

        # TODO: use Django settings to configure the logger
        # https://docs.djangoproject.com/en/3.1/topics/logging/
        logger = logging.getLogger()
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        formatter = logging.Formatter("{levelname:8} {message}", style="{")
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    def add_arguments(self, parser):
        """
        :param parser: an instance of :py:class:`argparse.ArgumentParser`
        """
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
        parser.add_argument("--workers", type=int, default=0,
                            help="number of workers for parallel processing (0 = use 1 worker per CPU core; default: %(default)s)")

    def handle(self, **kwargs):
        start = datetime.datetime.now(tz=datetime.timezone.utc)
        updated_pkgs, count_updated_pages = self.do_update(**kwargs)
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
        log.save()

    def do_update(self, *, cache_dir, workers,
                  force=False,
                  only_repos=None,
                  only_packages=None,
                  keep_tarballs=False,
                  **kwargs):
        finder = ManPagesFinder(cache_dir)
        finder.refresh()

        # everything in a single transaction
        with transaction.atomic():
            updated_pkgs = update_packages(finder, force=force, only_repos=only_repos)
            if only_packages is None:
                count_updated_pages = update_man_pages(finder, updated_pkgs)
            else:
                count_updated_pages = update_man_pages(finder, [p for p in updated_pkgs if p.name in only_packages])

        # this is called outside of the transaction, so that the cache can be reused on errors
        if keep_tarballs is False:
            finder.clear_pkgcache()

        # convert manual pages to plain-text
        # (one transaction per update, otherwise we might hit memory allocation error)
        def worker(man_id):
            man = ManPage.objects.get(id=man_id)
            try:
                man.get_converted("txt")
            except SoelimError as e:
                logger.error("SoelimError ({}) while converting {}.{}.{} to txt".format(str(e), man.name, man.section, man.lang))
            except subprocess.CalledProcessError as e:
                logger.error("CalledProcessError while converting {}.{}.{} to txt:\nreturncode = {}\nstderr = {}"
                             .format(man.name, man.section, man.lang, e.returncode, e.stderr))

        # prepare man page IDs which need to be converted
        # (queryset needs to be a list for multiprocessing to work)
        queryset = ManPage.objects.only("package", "lang", "content_id", "converted_content_id").filter(content__txt=None).values_list("id", flat=True)
        queryset = list(queryset)

        # all existing database connections have to be closed before forking,
        # each process will then recreate its own connection:
        # https://stackoverflow.com/a/10684672
        django.db.connections.close_all()

        # parallel processing of the queryset
        import concurrent.futures
        # FIXME: Why the fuck does it deadlock here, after we moved the code into the Command class?
        #        Database connections are closed just above, which used to work before...
        #with concurrent.futures.ProcessPoolExecutor(max_workers=workers or None) as executor:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers or None) as executor:
            executor.map(worker, queryset)

        # VACUUM cannot run inside a transaction block
        if updated_pkgs or only_packages is not None:
            logger.info("Running VACUUM FULL ANALYZE on our tables...")
            for Model in [Package, Content, ManPage, SymbolicLink]:
                table = Model.objects.model._meta.db_table
                logger.info("--> {}".format(table))
                with connection.cursor() as cursor:
                    cursor.execute("VACUUM FULL ANALYZE {};".format(table))

        return updated_pkgs, count_updated_pages
