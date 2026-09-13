"""Where the four command-line tools live when they are installed but not on PATH.

`scripts/doctor.py` uses this to say which folder to add, and `scripts/setup.py` uses it
to find a tool the installer just wrote and record that folder in `vault.config.json`.
The Windows installers are the reason this exists: the UB-Mannheim Tesseract package
never edits PATH, and winget's shims do not reach a terminal that was already open.

Standard library only, no imports from `src`, because both callers run before anything
is installed.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

# The binaries Media Vault shells out to, and what each one is for.
TOOLS = {'ffmpeg': 'video frames and audio', 'ffprobe': 'video metadata and durations',
         'magick': 'image dimensions, EXIF and GPS', 'tesseract': 'text in photos (optional)'}

# winget package ids, in the order setup installs them. One UAC prompt each.
WINGET = [('ffmpeg', 'Gyan.FFmpeg'), ('magick', 'ImageMagick.ImageMagick'),
          ('tesseract', 'UB-Mannheim.TesseractOCR')]

# Package manager, the command that installs all four, and how to detect the manager.
PACKAGE_COMMANDS = [
    ('pacman', ['sudo', 'pacman', '-S', '--needed', 'ffmpeg', 'imagemagick', 'tesseract', 'tesseract-data-eng']),
    ('apt', ['sudo', 'apt', 'install', '-y', 'ffmpeg', 'imagemagick', 'tesseract-ocr', 'tesseract-ocr-eng']),
    ('dnf', ['sudo', 'dnf', 'install', '-y', 'ffmpeg', 'ImageMagick', 'tesseract', 'tesseract-langpack-eng']),
    ('brew', ['brew', 'install', 'ffmpeg', 'imagemagick', 'tesseract']),
]


def package_command():
    """The install command for whichever package manager this machine has, or None."""
    for manager, command in PACKAGE_COMMANDS:
        if shutil.which(manager):
            return manager, command
    return None


def candidate_dirs():
    """Folders an installer may have written a tool into without putting it on PATH."""
    if os.name != 'nt':
        return [Path(p) for p in ('/usr/bin', '/usr/local/bin', '/opt/homebrew/bin', '/snap/bin') if Path(p).is_dir()]
    local = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local'))
    program_files = [Path(os.environ.get(key, default)) for key, default in
                     (('ProgramFiles', r'C:\Program Files'), ('ProgramFiles(x86)', r'C:\Program Files (x86)'))]
    found = [local / 'Microsoft/WinGet/Links', Path(r'C:\ProgramData\chocolatey\bin'), local / 'Programs/Tesseract-OCR']
    for base in program_files:
        found.append(base / 'Tesseract-OCR')
        found += sorted(base.glob('ImageMagick-*'))
    # winget keeps the real ffmpeg build under Packages; the Links shim usually covers it, this does not hurt.
    found += sorted((local / 'Microsoft/WinGet/Packages').glob('Gyan.FFmpeg*/*/bin'))
    return [p for p in found if p.is_dir()]


def locate(binary, dirs=None):
    """The folder holding ``binary`` off PATH, or None. Never looks at PATH itself."""
    name = binary + ('.exe' if os.name == 'nt' else '')
    for directory in (candidate_dirs() if dirs is None else dirs):
        if (Path(directory) / name).is_file():
            return Path(directory)
    return None


def discover(binaries=None, env=None):
    """{binary: folder} for every named tool that is installed somewhere PATH cannot see.

A tool already on PATH is left out: nothing needs recording for it."""
    env = os.environ if env is None else env
    dirs = candidate_dirs()
    found = {}
    for binary in (TOOLS if binaries is None else binaries):
        if shutil.which(binary, path=env.get('PATH')):
            continue
        directory = locate(binary, dirs)
        if directory is not None:
            found[binary] = directory
    return found


def extend_path(folders, env=None):
    """Put folders at the front of PATH in ``env``, skipping ones already there."""
    env = os.environ if env is None else env
    existing = [p for p in env.get('PATH', '').split(os.pathsep) if p]
    known = {os.path.normcase(p) for p in existing}
    extra = [str(f) for f in folders if os.path.normcase(str(f)) not in known]
    if extra:
        env['PATH'] = os.pathsep.join(extra + existing)
    return extra
