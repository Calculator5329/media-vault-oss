"""Temporary directories for the tests, on whatever disk the machine offers.

Set MEDIA_VAULT_TMP to put them somewhere specific (a small tmpfs /tmp fills up
under the video tests); otherwise the interpreter's default temp location is
used, which honours TMPDIR.
"""
import os
import tempfile
from pathlib import Path


def base():
    return os.environ.get("MEDIA_VAULT_TMP") or None


def scratch(prefix="media-vault-test-"):
    return Path(tempfile.mkdtemp(prefix=prefix, dir=base()))
