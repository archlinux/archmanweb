#! /usr/bin/env python3

import os.path
import datetime
import logging
import tarfile
import gzip
from pathlib import PurePath

import requests
import chardet
import pycman
import pyalpm

logger = logging.getLogger(__name__)

PACCONF = """
[options]
RootDir     = /
DBPath      = {pacdbpath}
CacheDir    = {cachedir}
LogFile     = {pacdbpath}
# Use system GPGDir so that we don't have to populate it
GPGDir      = /etc/pacman.d/gnupg/
Architecture = {arch}

# Repos needed for Template:Pkg checking

[core]
Include = /etc/pacman.d/mirrorlist

[extra]
Include = /etc/pacman.d/mirrorlist

[community]
Include = /etc/pacman.d/mirrorlist

[multilib]
Include = /etc/pacman.d/mirrorlist
"""

MANDIR = "usr/share/man/"

def decode(text):
    CHARSETS = ("utf8", "ascii", "iso-8859-1", "iso-8859-9", "iso-8859-15", "cp1250", "cp1252")
    for charset in CHARSETS:
        try:
            return text.decode(charset)
        except UnicodeDecodeError:
            pass

    # fall back to chardet and errors="replace"
    encoding = chardet.detect(text)["encoding"]
    return text.decode(encoding, errors="replace")

class ManPagesFinder:
    def __init__(self, tmpdir):
        self.tmpdir = os.path.abspath(os.path.join(tmpdir, "arch-manpages"))
        self.dbpath = os.path.join(self.tmpdir, "pacdbpath")
        self.cachedir = os.path.join(self.tmpdir, "cached_packages")

        os.makedirs(self.dbpath, exist_ok=True)
        os.makedirs(self.cachedir, exist_ok=True)

        self.sync_db = self.init_sync_db(PACCONF, arch="x86_64")
        self.files_db = {}
        self.init_files_db(self.sync_db)
        self._cached_tarfiles = {}

    def init_sync_db(self, config, arch):
        confpath = os.path.join(self.dbpath, "pacman.conf")
        f = open(confpath, "w")
        f.write(config.format(pacdbpath=self.dbpath,
                              cachedir=self.cachedir,
                              arch=arch))
        f.close()
        return pycman.config.init_with_config(confpath)

    def init_files_db(self, pacdb):
        dbpath = os.path.join(self.dbpath, "files")
        os.makedirs(dbpath, exist_ok=True)
        for db in pacdb.get_syncdbs():
            files_db = os.path.join(dbpath, "{}.files".format(db.name))
            if os.path.exists(files_db):
                local_timestamp = os.path.getmtime(files_db)
            else:
                local_timestamp = 0
            self.files_db.setdefault(db.name, {
                "path": files_db,
                "timestamp": local_timestamp,
            })

    # TODO: check integrity of the downloaded files
    def _refresh_files_db(self, db):
        for server in db.servers:
            for ext in [".tar.gz", ".tar.xz"]:
                url = os.path.join(server, db.name + ".files" + ext)
                r = requests.head(url)
                if r.status_code != 200:
                    continue

                # parse remote timestamp
                remote_timestamp = r.headers["last-modified"]
                remote_timestamp = datetime.datetime.strptime(remote_timestamp, '%a, %d %b %Y %X GMT')
                remote_timestamp = remote_timestamp.replace(tzinfo=datetime.timezone.utc).timestamp()

                # get local things
                local_db = self.files_db[db.name]
                local_timestamp = local_db["timestamp"]
                _path = os.path.join(os.path.dirname(local_db["path"]), db.name + ".files" + ext)

                # check if we need to update
                if remote_timestamp > local_timestamp:
                    r = requests.get(url, stream=True)
                    with open(_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=4096):
                            f.write(chunk)

                    # update timestamp
                    local_db["timestamp"] = remote_timestamp

                    # drop from cache
                    if local_db["path"] in self._cached_tarfiles:
                        del self._cached_tarfiles[local_db["path"]]

                    # create or update the symlink
                    if os.path.islink(local_db["path"]):
                        os.remove(local_db["path"])
                    os.symlink(db.name + ".files" + ext, local_db["path"])

                # return on success
                return

        raise Exception("Failed to sync files database for '{}'.".format(db.name))

    # sync databases like pacman -Sy + -Fs
    def _refresh_sync_db(self, pacdb, force=False):
        for db in pacdb.get_syncdbs():
            # since this is private pacman database, there is no locking
            db.update(force)

            # update files database
            self._refresh_files_db(db)

    # sync all
    def refresh(self):
        try:
            logger.info("Syncing pacman database (x86_64)...")
            self._refresh_sync_db(self.sync_db)
        except pyalpm.error:
            logger.exception("Failed to sync pacman database.")
            raise

    def get_man_files(self, pkg, repo=None):
        if repo is None:
            repo = [db for db in self.sync_db.get_syncdbs() if db.get_pkg(pkg.name)][0].name
        local_db = self.files_db[repo]["path"]
        t = self._cached_tarfiles.setdefault(local_db, tarfile.open(local_db, "r"))
        files = t.extractfile("{}-{}/files".format(pkg.name, pkg.version))

        for line in files.readlines():
            line = line.decode("utf-8").rstrip()
            if line.startswith(MANDIR) and not line.endswith("/"):
                yield line

    def get_all_man_files(self):
        for db in self.sync_db.get_syncdbs():
            for pkg in db.pkgcache:
                yield pkg, list(self.get_man_files(pkg, db.name))

    def _download_package(self, pkg):
        class Options:
            downloadonly = True
            nodeps = True
        o = Options
        t = pycman.transaction.init_from_options(self.sync_db, o)
        t.add_pkg(pkg)
        if not pycman.transaction.finalize(t):
            raise Exception("Pycman transaction failed: {}".format(t))

    def get_man_contents(self, pkg, *, keep_tarball=True):
        # first check if there are any man files at all to avoid useless downloads
        man_files = list(self.get_man_files(pkg))
        if not man_files:
            return

        # get the pkg tarball
        tarball = os.path.join(self.cachedir, "{}-{}-{}.pkg.tar.xz".format(pkg.name, pkg.version, pkg.arch))
        if not os.path.isfile(tarball):
            self._download_package(pkg)
        assert os.path.isfile(tarball)

        # extract man files
        t = tarfile.open(tarball, "r")
        for file in man_files:
            info = t.getmember(file)
            if info.issym():
                # TODO: treat symlinks as symlinks, they are extracted as normal files now
                # (affected packages: openssl)
                # TODO: t.extractfile() cannot extract broken symlinks, including absolute paths that would normally work
                # (affected packages: openjade, drbd-utils)
                logger.warning("Skipping symbolic link {}".format(file))
                continue

            man = t.extractfile(file).read()
            if file.endswith(".gz"):
                file = file[:-3]
                man = gzip.decompress(man)
            man = decode(man)
            # django complains, the DBMS would drop it anyway
            man = man.replace("\0", "")
            yield file, man
        t.close()

        # clean up
        if keep_tarball is False:
            os.remove(tarball)

    def get_all_man_contents(self, *, keep_tarballs=True):
        for db in self.sync_db.get_syncdbs():
            for pkg in db.pkgcache:
                for file, man in self.get_man_contents(pkg, keep_tarball=keep_tarballs):
                    yield pkg, file, man

    def pkg_exists(self, repo, pkgname):
        db = [db for db in self.sync_db.get_syncdbs() if db.name == repo][0]
        if db.get_pkg(pkgname) is not None:
            return True
        return False

if __name__ == "__main__":
    # init logging
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    formatter = logging.Formatter("{levelname:8} {message}", style="{")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    # init django
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mysite.settings")
    import django
    django.setup()
    from archweb_manpages.models import Package, ManPage

    finder = ManPagesFinder("./.cache")
    finder.refresh()

    # set of packages for which we'll need to update the man pages
    updated_pkgs = []

    # update packages in the django database
    for db in finder.sync_db.get_syncdbs():
        for pkg in db.pkgcache:
            result = Package.objects.filter(repo=db.name, name=pkg.name)
            assert len(result) in {0, 1}
            if len(result) == 0:
                db_package = Package()
                db_package.repo = db.name
                db_package.name = pkg.name
                db_package.version = pkg.version
                db_package.arch = pkg.arch
                db_package.save()
                updated_pkgs.append(pkg)
            else:
                db_package = result[0]
                if pyalpm.vercmp(db_package.version, pkg.version) == -1:
                    # db_package.version will be updated later, in the same transaction as the man pages
                    updated_pkgs.append(pkg)

    # delete old packages from the django database
    for db_package in Package.objects.order_by("repo").order_by("name"):
        if not finder.pkg_exists(db_package.repo, db_package.name):
            Package.objects.filter(repo=db_package.repo, name=db_package.name).delete()

    for pkg in updated_pkgs:
        db_pkg = Package.objects.filter(repo=pkg.db.name, name=pkg.name)[0]
        files = set(finder.get_man_files(pkg))
        if not files:
            continue

        # the files above include the .gz suffix, we need to collect even the
        # versions that enter the database
        paths = set()

        # insert/update man pages
        for path, content in finder.get_man_contents(pkg):
            # extract info from path, check if it makes sense
            pp = PurePath(path)
            man_name = pp.stem
            man_section = pp.suffix[1:]  # strip the dot
            pp = pp.relative_to(MANDIR)
            if pp.parts[0].startswith("man"):
                man_lang = "en"
            elif len(pp.parts) > 1 and pp.parts[1].startswith("man"):
                man_lang = pp.parts[0]
            else:
                logger.warning("Skipping path with unrecognized structure: {}".format(path))
                continue

            paths.add(path)
            result = ManPage.objects.filter(package_id=db_pkg.id, path=path)
            assert len(result) in {0, 1}
            if len(result) == 0:
                db_man = ManPage()
                db_man.package_id = db_pkg.id
                db_man.path = path
                db_man.name = man_name
                db_man.section = man_section
                db_man.lang = man_lang
            else:
                db_man = result[0]
            db_man.content = content
            db_man.html = None
            # TODO: this might still fail if there are multiple foo.1 in different directories and same language
            db_man.save()

        # delete man pages whose files no longer exist
        for db_man in ManPage.objects.filter(package_id=db_pkg.id):
            if db_man.path not in paths:
                ManPage.objects.filter(package_id=db_pkg.id, path=db_man.path).delete()

        # update pkg version
        db_pkg.version = pkg.version
        db_pkg.save()
