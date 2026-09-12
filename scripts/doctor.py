"""Media Vault doctor: what is installed, what is configured, what this machine can run.

    python scripts/doctor.py          human-readable report, one line per check
    python scripts/doctor.py --json   the same facts as JSON (agents read this)

Every line is a measurement or a clearly labelled suggestion. The exit code is 0 when the
vault can start, 1 when something required is missing. Nothing here downloads, installs or
writes files, and it imports only the standard library, because a fresh clone runs it before
anything else is installed.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / 'vault.config.json'
EXAMPLE_PATH = ROOT / 'vault.config.example.json'

# Every key the config may carry, with the value used when the key is absent. Anything else is an error.
DEFAULTS = {'sources': [], 'exports': None, 'tier': 'personal',
            'state_dir': '.catalog', 'models_dir': 'models', 'port': 8770, 'external_roots': None}
BASE_PACKAGES = [('PIL', 'Pillow'), ('numpy', 'numpy'), ('cv2', 'opencv-python-headless'),
                 ('onnxruntime', 'onnxruntime'), ('faster_whisper', 'faster-whisper'), ('av', 'av')]
OPTIONAL_PACKAGES = [('torch', 'vision search, video moments, descriptions'),
                     ('transformers', 'descriptions and quality scoring')]
MODELS = [('faces', 'face detection and grouping'), ('whisper-small', 'transcripts for video and audio'),
          ('siglip2', 'vision search and video moments'), ('gazetteer', 'place names for coordinates')]
# Where tesseract keeps its trained data, in the order the doctor looks after TESSDATA_PREFIX.
TESSDATA_DIRS = ['/usr/share/tessdata', '/usr/share/tesseract-ocr/5/tessdata',
                 '/usr/share/tesseract-ocr/4.00/tessdata', r'C:\Program Files\Tesseract-OCR\tessdata']
GATES = {'python', 'venv', 'ffmpeg', 'ffprobe', 'imagemagick', 'tesseract', 'config'} | {
    f'package {module}' for module, _ in BASE_PACKAGES}


def run(cmd, timeout=10):
    """First line of a command's output, or '' when it is missing, fails or hangs. Never raises."""
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ''
    text = (done.stdout or done.stderr or '').strip()
    return text.splitlines()[0].strip() if text else ''


def external_roots(settings=None):
    """Mount roots treated as removable: the config value, else src.portable, else the defaults."""
    configured = (settings or {}).get('external_roots')
    if configured is not None:
        return [Path(str(p)).resolve() for p in configured if str(p).strip()]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:  # src.portable is stdlib only, but a fresh clone may be run from another directory.
        return [Path(p) for p in importlib.import_module('src.portable').external_roots()]
    except Exception:
        value = os.environ.get('MEDIA_VAULT_EXTERNAL_ROOTS')
        if value is not None:
            return [Path(p).resolve() for p in value.split(os.pathsep) if p.strip()]
        return [] if os.name == 'nt' else [Path('/mnt'), Path('/run/media'), Path('/media')]


def under_root(path, roots):
    return any(Path(path) == root or root in Path(path).parents for root in roots)


def tessdata_english(env=None, extra_dirs=()):
    """Path to eng.traineddata, or None. TESSDATA_PREFIX first, then the usual install folders."""
    env = os.environ if env is None else env
    candidates = []
    prefix = env.get('TESSDATA_PREFIX')
    if prefix:  # Some packages point this at the parent of tessdata, some at tessdata itself.
        candidates += [Path(prefix), Path(prefix) / 'tessdata']
    binary = shutil.which('tesseract', path=env.get('PATH'))
    if binary:
        candidates.append(Path(binary).resolve().parent / 'tessdata')
    for directory in candidates + [Path(p) for p in (*extra_dirs, *TESSDATA_DIRS)]:
        try:
            if (directory / 'eng.traineddata').is_file():
                return directory / 'eng.traineddata'
        except OSError:
            continue
    return None


def validate_config(data, root=ROOT):
    """Return (settings, errors). settings carries resolved paths; errors is a list of strings."""
    if not isinstance(data, dict):
        return dict(DEFAULTS), ['vault.config.json must hold a JSON object']
    errors = ['unknown keys: ' + ', '.join(sorted(set(data) - set(DEFAULTS)))] if set(data) - set(DEFAULTS) else []
    settings = {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}

    sources, resolved = settings['sources'], []
    if not isinstance(sources, list) or not sources or not all(isinstance(s, str) and s.strip() for s in sources):
        errors.append('sources must be a non-empty list of absolute folder paths')
    else:
        for entry in sources:
            path = Path(entry).expanduser()
            if not path.is_absolute():
                errors.append(f'source is not an absolute path: {entry}')
                continue
            resolved.append(path := path.resolve())
            if not path.is_dir():
                errors.append(f'source folder does not exist: {path}')
            elif not os.access(path, os.R_OK | os.X_OK):
                errors.append(f'source folder is not readable: {path}')
        if len(set(resolved)) != len(resolved):
            errors.append('source folders must be distinct')

    exports, exports_path = settings['exports'], None
    if exports is not None:
        if not isinstance(exports, str) or not Path(exports).expanduser().is_absolute():
            errors.append('exports must be an absolute folder path, or null')
        elif not (exports_path := Path(exports).expanduser().resolve()).is_dir():
            errors.append(f'exports folder does not exist: {exports_path}')

    if settings['tier'] != 'personal':
        errors.append(f"tier must be \"personal\", found {settings['tier']!r}")
    port = settings['port']
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        errors.append(f'port must be a number between 1024 and 65535, found {port!r}')
    configured_roots = settings['external_roots']
    if configured_roots is not None and (not isinstance(configured_roots, list)
                                         or not all(isinstance(p, str) for p in configured_roots)):
        errors.append('external_roots must be a list of paths, or null')
        settings = {**settings, 'external_roots': None}

    roots, derived = external_roots(settings), {}
    for key in ('state_dir', 'models_dir'):
        value = settings[key]
        if not isinstance(value, str) or not value.strip():
            errors.append(f'{key} must be a folder path')
            derived[key] = (root / DEFAULTS[key]).resolve()
            continue
        path = Path(value).expanduser()
        derived[key] = path = path.resolve() if path.is_absolute() else (root / path).resolve()
        inside = [s for s in resolved + ([exports_path] if exports_path else []) if path == s or s in path.parents]
        if inside:
            errors.append(f'{key} {path} is inside {inside[0]}; derived state must stay outside originals')
        if under_root(path, roots):
            errors.append(f'{key} {path} is on removable media; derived state must stay on the local drive')

    settings.update(sources_resolved=resolved, exports_path=exports_path,
                    state_path=derived['state_dir'], models_path=derived['models_dir'])
    return settings, errors


def load_config(path=CONFIG_PATH, root=ROOT):
    """Return (settings, errors). A missing or unparseable file is an error row, never an exception."""
    path = Path(path)
    if not path.is_file():
        return validate_config({}, root)[0], [
            f'no config at {path}; copy {EXAMPLE_PATH.name} to {path.name} and set sources']
    try:
        return validate_config(json.loads(path.read_text(encoding='utf-8')), root)
    except (OSError, ValueError) as exc:
        return validate_config({}, root)[0], [f'{path.name} could not be read: {exc}']


def count(database, sql):
    """One number from a read-only sqlite query, or None when the file or table is not there."""
    try:
        with closing(sqlite3.connect(f'{Path(database).as_uri()}?mode=ro', uri=True)) as conn:
            return conn.execute(sql).fetchone()[0]
    except (sqlite3.Error, OSError, ValueError):
        return None


def hardware_facts(state_path):
    gpu = None
    if shutil.which('nvidia-smi'):
        parts = [p.strip() for p in run(['nvidia-smi', '--query-gpu=name,memory.total,memory.free',
                                         '--format=csv,noheader,nounits']).split(',')]
        if len(parts) == 3 and parts[1].isdigit():
            gpu = {'name': parts[0], 'memory_total_mb': int(parts[1]),
                   'memory_free_mb': int(parts[2]) if parts[2].isdigit() else None}
    if os.name == 'nt':
        total = run(['powershell', '-NoProfile', '-Command',
                     '(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory'], timeout=20)
        ram = round(int(total) / 1024 ** 3, 1) if total.isdigit() else None
    else:
        try:
            ram = round(int(re.search(r'MemTotal:\s+(\d+)', Path('/proc/meminfo').read_text()).group(1)) / 1024 ** 2, 1)
        except (OSError, AttributeError, ValueError):
            ram = None
    probe = Path(state_path)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        free = round(shutil.disk_usage(probe).free / 1024 ** 3, 1)
    except OSError:
        free = None
    return {'gpu': gpu, 'ram_gb': ram, 'cpu_count': os.cpu_count(), 'disk_free_gb': free, 'disk_path': str(state_path)}


def suggest(hardware):
    """Which optional models make sense here. CPU stages suit any machine; the GPU ones do not."""
    gpu = hardware['gpu']
    vram = round(gpu['memory_total_mb'] / 1024, 1) if gpu else None
    big = bool(vram and vram >= 6)
    return {'vram_gb': vram, 'items': [
        {'model': 'faces', 'recommended': True, 'why': 'Runs on CPU everywhere.'},
        {'model': 'whisper-small', 'recommended': True, 'why': 'Runs on CPU everywhere, slower without a GPU.'},
        {'model': 'gazetteer', 'recommended': True, 'why': 'A small offline place-name table, CPU only.'},
        {'model': 'siglip2', 'recommended': bool(gpu),
         'why': f'{gpu["name"]} present, indexing is fast.' if gpu
         else 'No NVIDIA GPU found. Worth it only if slow CPU indexing is acceptable.'},
        {'model': 'descriptions and quality', 'recommended': big,
         'why': f'{vram} GB VRAM is enough.' if big
         else f'Needs an NVIDIA GPU with 6 GB or more; this one has {vram} GB.' if vram
         else 'Needs an NVIDIA GPU with 6 GB or more; none found.'}]}


def check_server(port):
    """The viewer rejects any Host but the loopback one, so send it explicitly."""
    request = urllib.request.Request(f'http://127.0.0.1:{port}/api/summary', headers={'Host': f'127.0.0.1:{port}'})
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            body = json.loads(response.read().decode('utf-8', 'replace'))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return {'running': False, 'files': None}
    return {'running': True, 'files': body.get('files') if isinstance(body, dict) else None}


def collect(settings, errors):
    """Build the report rows. Each row is a name, a status and a detail sentence."""
    rows = []

    def add(name, status, detail):
        rows.append({'name': name, 'status': status, 'detail': detail})

    supported = (3, 11) <= sys.version_info[:2] <= (3, 13)
    add('python', 'OK' if supported else 'WARN', f'{platform.python_version()} at {sys.executable}' + (
        '' if supported else '; onnxruntime and ctranslate2 wheels may not exist outside 3.11 to 3.13'))

    venv = ROOT / ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python')
    active = venv.is_file() and Path(sys.executable).resolve() == venv.resolve()
    add('venv', 'OK' if venv.is_file() else 'MISSING',
        f'{venv} ' + ('(running it now)' if active else '(present, not the interpreter running this)')
        if venv.is_file() else f'no interpreter at {venv}; create .venv and install requirements.txt')

    for module, package in BASE_PACKAGES:
        try:
            importlib.import_module(module)
            add(f'package {module}', 'OK', package)
        except Exception as exc:  # ImportError or a broken native wheel; both mean not usable.
            add(f'package {module}', 'MISSING', f'{package} ' + ('not installed' if isinstance(exc, ImportError)
                                                                 else f'fails to load: {type(exc).__name__}'))
    for module, unlocks in OPTIONAL_PACKAGES:
        try:
            loaded = importlib.import_module(module)
        except Exception:
            add(f'package {module}', 'WARN', f'not installed; needed for {unlocks}')
            continue
        detail = getattr(loaded, '__version__', 'present')
        if module == 'torch':
            try:
                detail += ', CUDA ' + (loaded.cuda.get_device_name(0) if loaded.cuda.is_available() else 'not available')
            except Exception:
                detail += ', CUDA state unknown'
        add(f'package {module}', 'OK', detail)

    for binary in ('ffmpeg', 'ffprobe'):
        found = shutil.which(binary)
        version = re.search(rf'{binary} version \S+', run([binary, '-version'])) if found else None
        add(binary, 'OK' if found else 'MISSING', f'{found} ({version.group(0) if version else "version unknown"})'
            if found else 'not on PATH; install ffmpeg')

    identify, magick = shutil.which('identify'), shutil.which('magick')
    if identify:
        add('imagemagick', 'OK', f'identify at {identify}' + (f', magick at {magick}' if magick else ''))
    elif magick:
        add('imagemagick', 'WARN',
            f'only magick at {magick}; src/probe.py calls identify, so the launcher needs an identify shim')
    else:
        add('imagemagick', 'MISSING', 'neither identify nor magick on PATH; install ImageMagick')

    data = tessdata_english()
    if not shutil.which('tesseract'):
        add('tesseract', 'MISSING', 'not on PATH; install tesseract for photo text search')
    elif data is None:
        add('tesseract', 'MISSING', f'{run(["tesseract", "--version"])}, but eng.traineddata was not found; '
                                    'install the English data or set TESSDATA_PREFIX')
    else:
        add('tesseract', 'OK', f'{run(["tesseract", "--version"])}, English data at {data}')

    add('config', 'MISSING' if errors else 'OK', '; '.join(errors) if errors else
        f'{CONFIG_PATH.name}: {len(settings["sources_resolved"])} source(s), primary '
        f'{settings["sources_resolved"][0]}, port {settings["port"]}, state {settings["state_path"]}'
        + (f', exports {settings["exports_path"]}' if settings['exports_path'] else ''))

    catalog_db = settings['state_path'] / 'catalog.db'
    imports_db = settings['state_path'] / 'imports.db'
    if not catalog_db.is_file():
        add('state', 'OPTIONAL', f'no catalog at {catalog_db}; not scanned yet')
    else:
        files = count(catalog_db, 'SELECT count(*) FROM files WHERE present=1')
        for extra in sorted((settings['state_path'] / 'source-catalogs').glob('*.db')):
            more = count(extra, 'SELECT count(*) FROM files WHERE present=1')
            if files is not None and more is not None:
                files += more
        verified = count(imports_db, 'SELECT count(*) FROM occurrences WHERE present=1 AND content_hash IS NOT NULL')
        add('state', 'OK', f'scanned: {files if files is not None else "unknown"} files across {len(settings["sources_resolved"])} source(s)'
            + (f', {verified} verified contents' if verified is not None else ', imports.db not built yet'))

    for name, unlocks in MODELS:
        folder = settings['models_path'] / name
        add(f'model {name}', 'OK' if (folder / 'acquisition.json').is_file() else 'OPTIONAL',
            f'not downloaded; unlocks {unlocks}' if not folder.is_dir() else
            f'{folder}; unlocks {unlocks}' if (folder / 'acquisition.json').is_file() else
            f'{folder} exists but has no acquisition.json; unlocks {unlocks}')

    server = check_server(settings['port'])
    add('server', 'OK' if server['running'] else 'OPTIONAL',
        f'running on port {settings["port"]} ({server["files"]} files)' if server['running']
        else f'not running on port {settings["port"]}')
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--json', action='store_true', help='print the report as JSON')
    args = parser.parse_args(argv)

    settings, errors = load_config()
    rows = collect(settings, errors)
    hardware = hardware_facts(settings['state_path'])
    suggestion = suggest(hardware)
    # Models, state and the server never gate; a WARN row is a note, not a blocker.
    failed = [r for r in rows if r['name'] in GATES and r['status'] == 'MISSING']
    ready = not failed
    following = (f'fix {failed[0]["name"]} ({failed[0]["detail"]})' if failed else
                 'run python vault.py to open the viewer' if (settings['state_path'] / 'catalog.db').is_file()
                 else 'run python vault.py scan to build the catalog')

    if args.json:
        print(json.dumps({'rows': rows, 'ready': ready, 'suggestion': suggestion, 'hardware': hardware,
                          'platform': {'os': platform.system(), 'release': platform.release(),
                                       'machine': platform.machine()},
                          'config_errors': errors, 'next': following}, indent=2))
        return 0 if ready else 1

    gpu = hardware['gpu']
    print(f'Media Vault doctor: {platform.system()} {platform.release()} {platform.machine()}')
    for row in rows:
        print(f'{row["status"]:<9}{row["name"]:<25}{row["detail"]}')
    print(f'{"INFO":<9}{"hardware":<25}'
          + (f'GPU {gpu["name"]}, {round(gpu["memory_total_mb"] / 1024, 1)} GB VRAM' if gpu else 'no NVIDIA GPU found')
          + f'; {hardware["cpu_count"]} CPUs; RAM {hardware["ram_gb"] or "unknown"} GB'
          + f'; {hardware["disk_free_gb"] or "unknown"} GB free at {hardware["disk_path"]}')
    for item in suggestion['items']:
        print(f'{"SUGGEST":<9}{item["model"]:<25}'
              + ('worth installing. ' if item['recommended'] else 'skip for now. ') + item['why'])
    print('READY: the vault can start.' if ready else 'NOT READY: fix the MISSING lines above, then run doctor again.')
    print('Next: ' + following)
    return 0 if ready else 1


if __name__ == '__main__':
    sys.exit(main())
