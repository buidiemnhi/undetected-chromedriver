#!/usr/bin/env python3
# this module is part of undetected_chromedriver

from distutils.version import LooseVersion
import io
import logging
import os
import random
import re
import secrets
import shutil
import string
import subprocess
import sys
import time
from urllib.request import urlopen
from urllib.request import urlretrieve
import zipfile


logger = logging.getLogger(__name__)

IS_POSIX = sys.platform.startswith(("darwin", "cygwin", "linux", "linux2"))


class Patcher(object):
    url_repo = "https://chromedriver.storage.googleapis.com"
    zip_name = "chromedriver_%s.zip"
    exe_name = "chromedriver%s"

    platform = sys.platform
    if platform.endswith("win32"):
        zip_name %= "win32"
        exe_name %= ".exe"
    if platform.endswith(("linux", "linux2")):
        zip_name %= "linux64"
        exe_name %= ""
    if platform.endswith("darwin"):
        zip_name %= "mac64"
        exe_name %= ""

    if platform.endswith("win32"):
        d = "~/appdata/roaming/undetected_chromedriver"
    elif "LAMBDA_TASK_ROOT" in os.environ:
        d = "/tmp/undetected_chromedriver"
    elif platform.startswith(("linux", "linux2")):
        d = "~/.local/share/undetected_chromedriver"
    elif platform.endswith("darwin"):
        d = "~/Library/Application Support/undetected_chromedriver"
    else:
        d = "~/.undetected_chromedriver"
    data_path = os.path.abspath(os.path.expanduser(d))

    def __init__(self, executable_path=None, force=False, version_main: int = 0):
        """
        Args:
            executable_path: None = automatic
                             a full file path to the chromedriver executable
            force: False
                    terminate processes which are holding lock
            version_main: 0 = auto
                specify main chrome version (rounded, ex: 82)
        """
        self.force = force
        self._custom_exe_path = False
        prefix = f"undetected{secrets.token_hex( 4 )}"

        if not os.path.exists(self.data_path):
            os.makedirs(self.data_path, exist_ok=True)

        if not executable_path:
            self.executable_path = os.path.join(
                self.data_path, "_".join([prefix, self.exe_name])
            )

        if not IS_POSIX:
            if executable_path:
                if not executable_path[-4:] == ".exe":
                    executable_path += ".exe"

        self.zip_path = os.path.join(self.data_path, prefix)

        if not executable_path:
            self.executable_path = os.path.abspath(
                os.path.join(".", self.executable_path)
            )

        if executable_path:
            self._custom_exe_path = True
            self.executable_path = executable_path

        self.version_main = version_main
        self.version_full = None

    def auto(self, executable_path=None, force=False, version_main=None):
        if executable_path:
            self.executable_path = executable_path
            self._custom_exe_path = True

        if self._custom_exe_path:
            ispatched = self.is_binary_patched(self.executable_path)
            if not ispatched:
                return self.patch_exe()
            else:
                return

        if version_main:
            self.version_main = version_main
        if force is True:
            self.force = force
        try:
            os.unlink(self.executable_path)
        except PermissionError:
            if self.force:
                self.force_kill_instances(self.executable_path)
                return self.auto(force=not self.force)
            try:
                if self.is_binary_patched():
                    # assumes already running AND patched
                    return True
            except PermissionError:
                pass
            # return False
        except FileNotFoundError:
            pass

        release = self.fetch_release_number()
        self.version_main = release.version[0]
        self.version_full = release

        for file in os.listdir(self.data_path):
            match = re.search("undetected(.+)driver(.+)?", file)
            if not match:
                logger.debug(" no match: %s" % file)
                continue
            pth = os.path.join(self.data_path, match[0])
            logger.debug(
                "found existing driver here: %s. \n"
                "checking if we can use this one, instead of downloading a new package"
                % pth
            )
            if self.is_binary_patched(pth):
                version_match = re.search(
                    "[\d]+",
                    subprocess.check_output([pth, "--version"], encoding="utf-8"),
                )
                if version_match:
                    pthver = int(version_match[0])
                    if self.version_main == pthver:
                        logger.debug(
                            "yep, we will make a copy so we can skip downloading"
                        )
                        shutil.copyfile(pth, self.executable_path)
                        return True

        self.unzip_package(self.fetch_package())
        return self.patch()

    def patch(self):
        self.patch_exe()
        return self.is_binary_patched()

    def fetch_release_number(self):
        """
        Gets the latest major version available, or the latest major version of self.target_version if set explicitly.
        :return: version string
        :rtype: LooseVersion
        """
        path = "/latest_release"
        if self.version_main:
            path += f"_{self.version_main}"
        path = path.upper()
        logger.debug("getting release number from %s" % path)
        return LooseVersion(urlopen(self.url_repo + path).read().decode())

    def parse_exe_version(self):
        with io.open(self.executable_path, "rb") as f:
            for line in iter(lambda: f.readline(), b""):
                match = re.search(rb"platform_handle\x00content\x00([0-9.]*)", line)
                if match:
                    return LooseVersion(match[1].decode())

    def fetch_package(self):
        """
        Downloads ChromeDriver from source

        :return: path to downloaded file
        """
        u = "%s/%s/%s" % (self.url_repo, self.version_full.vstring, self.zip_name)
        logger.debug("downloading from %s" % u)
        # return urlretrieve(u, filename=self.data_path)[0]
        return urlretrieve(u)[0]

    def unzip_package(self, fp):
        """
        Does what it says

        :return: path to unpacked executable
        """
        logger.debug("unzipping %s" % fp)
        try:
            os.unlink(self.zip_path)
        except (FileNotFoundError, OSError):
            pass

        os.makedirs(self.zip_path, mode=0o755, exist_ok=True)
        with zipfile.ZipFile(fp, mode="r") as zf:
            zf.extract(self.exe_name, self.zip_path)
        try:
            os.rename(os.path.join(self.zip_path, self.exe_name), self.executable_path)
            os.remove(fp)
        except PermissionError:
            # file in use, ignore and pass
            # as same file can be used by multiple instance
            pass
        try:
            os.rmdir(self.zip_path)
            os.chmod(self.executable_path, 0o755)
        except PermissionError:
            #  sometimes: no access on the path, or driver still running
            #  in other process
            #  ignore. so start using the existing file
            pass
        return self.executable_path

    @staticmethod
    def force_kill_instances(exe_name):
        """
        kills running instances.
        :param: executable name to kill, may be a path as well

        :return: True on success else False
        """
        exe_name = os.path.basename(exe_name)
        if IS_POSIX:
            r = os.system("kill -f -9 $(pidof %s)" % exe_name)
        else:
            r = os.system("taskkill /f /im %s" % exe_name)
        return not r

    @staticmethod
    def gen_random_cdc():
        cdc = random.choices(string.ascii_letters, k=27)
        return "".join(cdc).encode()

    def is_binary_patched(self, executable_path=None):
        executable_path = executable_path or self.executable_path
        try:
            with io.open(executable_path, "rb") as fh:
                return fh.read().find(b"undetected chromedriver") != -1
        except FileNotFoundError:
            return False

    def patch_exe(self):
        start = time.perf_counter()
        logger.info("patching driver executable %s" % self.executable_path)
        start = time.time()

        def gen_js_whitespaces(match):
            return b"\n" * len(match.group())

        def gen_call_function_js_cache_name(match):
            rep_len = len(match.group()) - 3
            ran_len = random.randint(6, rep_len)
            bb = b"'" + bytes(str().join(random.choices(population=string.ascii_letters, k=ran_len)), 'ascii') + b"';" \
                 + (b"\n" * (rep_len - ran_len))
            return bb
        with io.open(self.executable_path, "r+b") as fh:

            content = fh.read()
            content = re.sub(b"window\.cdc_[a-zA-Z0-9]{22}_(Array|Promise|Symbol) = window\.(Array|Promise|Symbol);",
                              gen_js_whitespaces, content)
            content = re.sub(b"window\.cdc_[a-zA-Z0-9]{22}_(Array|Promise|Symbol) \|\|", gen_js_whitespaces, content)
            content = re.sub(b"'\\$cdc_[a-zA-Z0-9]{22}_';", gen_call_function_js_cache_name, content)
            content = re.sub(rb"\$cdc_[a-zA-Z0-9]{22}_", lambda m: bytes(
                random.choices((string.ascii_letters + string.digits).encode("ascii"), k=len(m.group()))), content)
            fh.seek(0)
            fh.write(content)
            match_injected_codeblock = re.search(rb"\{window\.cdc.*?;\}", content)
            if match_injected_codeblock:
                target_bytes = match_injected_codeblock[0]
                new_target_bytes = (
                    b'{console.log("undetected chromedriver 1337!")}'.ljust(
                        len(target_bytes), b" "
                    )
                )

                new_content = content.replace(target_bytes, new_target_bytes)
                if new_content == content:
                    logger.warning(
                        "something went wrong patching the driver binary. could not find injection code block"
                    )
                else:
                    logger.debug(
                        "found block:\n%s\nreplacing with:\n%s"
                        % (target_bytes.strip(), new_target_bytes.strip())
                    )
                fh.seek(0)
                fh.write(new_content)
        logger.debug(
            "patching took us {:.2f} seconds".format(time.perf_counter() - start)
        )

    def __repr__(self):
        return "{0:s}({1:s})".format(
            self.__class__.__name__,
            self.executable_path,
        )

    def __del__(self):
        if self._custom_exe_path:
            # if the driver binary is specified by user
            # we assume it is important enough to not delete it
            return
        else:
            timeout = 1  # stop trying after this many seconds
            t = time.monotonic()
            while True:
                now = time.monotonic()
                if now - t > timeout:
                    # we don't want to wait until the end of time
                    logger.debug(
                        "could not unlink %s within the timeout window (%d seconds)"
                        % (self.executable_path, timeout)
                    )
                    break
                try:
                    os.unlink(self.executable_path)
                    logger.debug(
                        "successfully unlinked %s after %.3f seconds"
                        % (self.executable_path, now - t)
                    )
                    break
                except (OSError, RuntimeError, PermissionError) as e:
                    logger.debug(
                        "cuold not unlink %s because of %s . remaining seconds left: %.3f "
                        % (self.executable_path, e, timeout - (now - t))
                    )
                    time.sleep(0.1)
                    continue
                except FileNotFoundError:
                    break
