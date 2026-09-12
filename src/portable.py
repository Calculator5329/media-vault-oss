"""The two places the code used to assume Linux: file locks and mount roots.

Locks: ``lock(stream, shared=False, blocking=True)`` takes an advisory lock on
an open file object. On POSIX it is ``fcntl.flock``; on Windows it is
``msvcrt.locking`` over the first byte, which is exclusive only, so a shared
request becomes exclusive there. A non-blocking request that loses raises
``BlockingIOError`` on both platforms, which is what every caller catches.

Mount roots: derived state (catalogs, caches, previews, corrections) must not
land on removable or network media that may vanish mid-write, and never inside
a source folder. ``external_roots()`` is the list of mount roots treated as
removable. The default on Linux is ``/mnt``, ``/run/media`` and ``/media``; on
Windows there is no default because drive letters carry no such signal.
``MEDIA_VAULT_EXTERNAL_ROOTS`` (paths separated by ``os.pathsep``) replaces the
list; an empty value disables the guard. ``vault.py`` sets the variable from the
``external_roots`` key in ``vault.config.json`` so the two never disagree.
"""
import os
from pathlib import Path
import sys
import time

_WINDOWS = sys.platform == 'win32'

if _WINDOWS:
    import msvcrt
else:
    import fcntl


def lock(stream, shared=False, blocking=True):
    """Advisory lock on an open file object. Released when the file closes."""
    if not _WINDOWS:
        flags = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB
        fcntl.flock(stream, flags)
        return
    fd = stream.fileno()
    if not blocking:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise BlockingIOError(str(exc)) from exc
        return
    # LK_LOCK retries for about ten seconds; keep trying so blocking means blocking.
    while True:
        try:
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            return
        except OSError:
            time.sleep(0.2)


def unlock(stream):
    if _WINDOWS:
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        fcntl.flock(stream, fcntl.LOCK_UN)


_DEFAULT_ROOTS = () if _WINDOWS else ('/mnt', '/run/media', '/media')


def external_roots():
    value = os.environ.get('MEDIA_VAULT_EXTERNAL_ROOTS')
    if value is None:
        return [Path(p) for p in _DEFAULT_ROOTS]
    return [Path(p).resolve() for p in value.split(os.pathsep) if p.strip()]


def on_external_root(path):
    """True when ``path`` (resolved) is one of the external roots or under one."""
    path = Path(path)
    return any(path == root or root in path.parents for root in external_roots())


LOCAL_STATE_MESSAGE = 'must stay on the local drive, outside removable media'


def assert_local_state(path, what='Derived state'):
    """Raise ``ValueError`` when ``path`` sits on an external root. Returns the resolved path."""
    resolved = Path(path).resolve()
    if on_external_root(resolved):
        raise ValueError(f'{what} {LOCAL_STATE_MESSAGE}')
    return resolved
