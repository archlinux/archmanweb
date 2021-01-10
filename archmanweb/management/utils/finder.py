#! /usr/bin/env python3

import os
from pathlib import Path
import shutil
import datetime
import logging
import gzip

import requests
import pycman
import pyalpm
import xtarfile as tarfile  # wrapper around tarfile - needed for zst packages

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

class ManPagesFinder:
    def __init__(self, topdir):
        topdir = Path(topdir).resolve()
        self.dbpath = topdir / "pacdbpath"
        self.cachedir = topdir / "cached_packages"

        os.makedirs(self.dbpath, exist_ok=True)
        os.makedirs(self.cachedir, exist_ok=True)

        self.sync_db = self.init_sync_db(PACCONF, arch="x86_64")
        self.files_db = {}
        self.init_files_db(self.sync_db)
        self._cached_tarfiles = {}

    def init_sync_db(self, config, arch):
        confpath = self.dbpath / "pacman.conf"
        f = open(confpath, "w")
        f.write(config.format(pacdbpath=self.dbpath,
                              cachedir=self.cachedir,
                              arch=arch))
        f.close()
        return pycman.config.init_with_config(confpath)

    def init_files_db(self, pacdb):
        dbpath = self.dbpath / "files"
        os.makedirs(dbpath, exist_ok=True)
        for db in pacdb.get_syncdbs():
            files_db = dbpath / "{}.files".format(db.name)
            if files_db.exists():
                local_timestamp = os.path.getmtime(files_db)
            else:
                local_timestamp = 0
            self.files_db.setdefault(db.name, {
                "path": files_db,
                "timestamp": local_timestamp,
            })

    # TODO: working with the files databases is not implemented in pyalpm: https://gitlab.archlinux.org/archlinux/pyalpm/-/issues/6
    # TODO: check integrity of the downloaded files
    def _refresh_files_db(self, db):
        for server in db.servers:
            for ext in [".tar.gz", ".tar.xz"]:
                url = server + "/" + db.name + ".files" + ext
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
                _path = Path(local_db["path"]).parent / (db.name + ".files" + ext)

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
                    if Path(local_db["path"]).is_symlink():
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

    def clear_pkgcache(self):
        # TODO: we should call pyalpm to do the equivalent of "pacman -Scc", but it's not implemented there
        shutil.rmtree(self.cachedir)

    def get_man_files(self, pkg, repo=None):
        if repo is None:
            repo = [db for db in self.sync_db.get_syncdbs() if db.get_pkg(pkg.name)][0].name
        local_db = self.files_db[repo]["path"]
        t = self._cached_tarfiles.setdefault(local_db, tarfile.open(str(local_db.resolve()), "r"))
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
        t = pycman.transaction.init_from_options(self.sync_db, Options)

        # reset callback functions which print lots of text into the logs
        def _void_cb(*args):
            pass
        self.sync_db.dlcb = _void_cb
        self.sync_db.eventcb = _void_cb
        self.sync_db.questioncb = _void_cb
        self.sync_db.progresscb = _void_cb

        t.add_pkg(pkg)
        if not pycman.transaction.finalize(t):
            raise Exception("Pycman transaction failed: {}".format(t))

    def get_man_contents(self, pkg):
        """
        Note: the content is yielded as `bytes`, its decoding is not a priori known
        """
        # first check if there are any man files at all to avoid useless downloads
        man_files = list(self.get_man_files(pkg))
        if not man_files:
            return

        # get the pkg tarball
        _pattern = "{}-{}-{}.pkg.tar.*".format(pkg.name, pkg.version, pkg.arch)
        if not list(f for f in self.cachedir.glob(_pattern) if not str(f).endswith(".part")):
            self._download_package(pkg)
        tarballs = sorted(f for f in self.cachedir.glob(_pattern) if not str(f).endswith(".part"))
        assert len(tarballs) > 0, _pattern
        tarball = tarballs[0]

        # extract man files
        with tarfile.open(str(tarball), "r") as t:
            hardlinks = []
            for file in man_files:
                info = t.getmember(file)
                # Hardlinks on the filesystem level are indifferentiable from normal files,
                # but in tar the first file added is "file" and the subsequent are hardlinks.
                # To make sure that normal files are processed first, we postpone yielding of
                # the hardlinks.
                if info.islnk():
                    if file.endswith(".gz"):
                        file = file[:-3]
                    target = info.linkname
                    if target.endswith(".gz"):
                        target = target[:-3]
                    hardlinks.append( ("hardlink", file, target) )
                elif info.issym():
                    if file.endswith(".gz"):
                        file = file[:-3]
                    target = info.linkname
                    if target.endswith(".gz"):
                        target = target[:-3]
                    yield "symlink", file, target
                else:
                    man = t.extractfile(file).read()
                    if file.endswith(".gz"):
                        file = file[:-3]
                        man = gzip.decompress(man)
                    yield "file", file, man
            yield from hardlinks

    def get_all_man_contents(self):
        for db in self.sync_db.get_syncdbs():
            for pkg in db.pkgcache:
                for v1, v2, v3 in self.get_man_contents(pkg):
                    yield pkg, v1, v2, v3

    def pkg_exists(self, repo, pkgname):
        db = [db for db in self.sync_db.get_syncdbs() if db.name == repo][0]
        if db.get_pkg(pkgname) is not None:
            return True
        return False
