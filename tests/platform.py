"""What this machine lets the tests do, so a platform limit skips rather than fails.

A Windows account without developer mode cannot create a symbolic link, NTFS matches
filenames case-insensitively, and systemd units are a Linux concept. Tests that depend
on one of those describe the platform, not a bug, so they carry a decorator from here.
Each capability is measured once, never guessed from the OS name, except the systemd one
which genuinely is about the operating system.
"""
import os
import tempfile
import unittest
from pathlib import Path


def _symlinks_work():
    with tempfile.TemporaryDirectory() as directory:
        try:
            (Path(directory) / 'link').symlink_to(Path(directory))
        except (OSError, NotImplementedError):
            return False
    return True


def _filesystem_is_case_sensitive():
    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / 'Case').mkdir()
        return not (Path(directory) / 'case').exists()


SYMLINKS = _symlinks_work()
CASE_SENSITIVE = _filesystem_is_case_sensitive()

requires_symlinks = unittest.skipUnless(
    SYMLINKS, 'creating a symbolic link needs administrator rights or developer mode on Windows')
requires_case_sensitive_filesystem = unittest.skipUnless(
    CASE_SENSITIVE, 'this filesystem matches filenames case-insensitively, so two case twins are one folder')
requires_systemd_platform = unittest.skipIf(
    os.name == 'nt', 'systemd units are rendered for Linux only; Windows uses scan --watch')
