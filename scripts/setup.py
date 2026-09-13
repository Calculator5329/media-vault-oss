#!/usr/bin/env python3
"""Media Vault setup: one command from a fresh clone to a viewer full of photos.

    python scripts/setup.py "D:\\Pictures"

That is the whole thing. It installs the command-line tools, builds the virtual
environment, writes vault.config.json for the folder you named, downloads the small
models, scans the library, runs enrichment until nothing is left, labels places, and
prints the viewer URL. Every step measures first and skips itself when it is already
done, so running it again after a failure, a reboot or a new model picks up where it
stopped.

    --vision            also install torch and SigLIP2 for search by description (several GB)
    --models CHOICE     standard (faces, gazetteer, whisper), all, or none. Default: standard
    --no-tools          never install packages; report what is missing instead
    --no-enrich         stop after the scan
    --serve             leave the viewer running when everything is done
    --port N            viewer port (default 8770)
    --state-dir P       where the catalog goes (default .catalog)
    --models-dir P      where models go (default models)
    --repair-path       only look for installed tools PATH cannot see, record them, exit
    --json              print one JSON line per step as well as the human report

Nothing here uploads anything. The network is used for package installs and model
downloads only, and the source folders are opened read-only.

Standard library only: this runs with whatever Python is on the machine, before the
virtual environment exists.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tools  # noqa: E402  sibling module, standard library only

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'vault.config.json'
VENV = ROOT / '.venv'
VENV_PYTHON = VENV / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
SUPPORTED = ((3, 11), (3, 13))
BASE_IMPORTS = 'PIL,numpy,cv2,onnxruntime,faster_whisper,av'
# Every key vault.config.json accepts besides "sources", with the value setup writes when
# the file does not have one yet. src/config.py is the authority on what is valid.
CONFIG_DEFAULTS = {'exports': None, 'tier': 'personal', 'state_dir': '.catalog', 'models_dir': 'models',
                   'port': 8770, 'external_roots': None, 'tool_paths': None}
MODEL_SETS = {'standard': ['--faces', '--gazetteer', '--whisper'], 'all': ['--all'], 'none': []}
# Downloaded bytes, so the report can say what a step is about to cost.
MODEL_SIZES = {'--faces': '37 MB', '--gazetteer': '13 MB', '--whisper': '461 MB',
               '--vision': '1.4 GB', '--all': '1.9 GB'}


class Stop(Exception):
    """A step could not finish and setup cannot usefully continue. The message says what to do."""


class Report:
    """The running record: one line per step on screen, and JSON lines when asked."""

    def __init__(self, total, as_json=False):
        self.as_json = as_json
        self.total = total
        self.done = 0
        self.steps = []
        self.started = time.monotonic()

    def begin(self, name, detail=''):
        self.done += 1
        print(f'\n[{self.done}/{self.total}] {name}' + (f': {detail}' if detail else ''), flush=True)
        return time.monotonic()

    def end(self, name, status, detail, started):
        seconds = round(time.monotonic() - started, 1)
        self.steps.append({'step': name, 'status': status, 'detail': detail, 'seconds': seconds})
        print(f'    {status}: {detail} ({seconds}s)', flush=True)
        if self.as_json:
            print(json.dumps(self.steps[-1]), flush=True)

    def finish(self, ready, detail):
        summary = {'setup': 'complete' if ready else 'incomplete', 'detail': detail,
                   'seconds': round(time.monotonic() - self.started, 1), 'steps': self.steps}
        if self.as_json:
            print(json.dumps(summary), flush=True)
        return summary


def stream(args, **kwargs):
    """Run a command with its output going straight to this terminal. Returns the exit code."""
    def short(value):
        text = str(value)
        try:  # Everything runs inside the repository; full paths make this log unreadable.
            return str(Path(text).relative_to(ROOT)) if Path(text).is_absolute() else text
        except ValueError:
            return text

    printable = ' '.join(short(a) for a in args)
    print(f'    $ {printable}', flush=True)
    try:
        return subprocess.call([str(a) for a in args], cwd=str(ROOT), **kwargs)
    except OSError as exc:
        raise Stop(f'could not run {printable}: {exc}') from exc


def capture(args, timeout=60):
    """(exit code, output) for a short command. Never raises."""
    try:
        done = subprocess.run([str(a) for a in args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return 1, ''
    return done.returncode, (done.stdout or '') + (done.stderr or '')


def python_version(executable):
    """(major, minor) for an interpreter, or None when it will not run."""
    code, out = capture([executable, '-c', 'import sys;print("%d.%d"%sys.version_info[:2])'], timeout=30)
    if code or '.' not in out:
        return None
    try:
        major, minor = out.strip().splitlines()[-1].split('.')[:2]
        return int(major), int(minor)
    except ValueError:
        return None


def supported(version):
    return version is not None and SUPPORTED[0] <= version <= SUPPORTED[1]


def base_interpreter():
    """An interpreter this project's wheels exist for, or None.

onnxruntime and ctranslate2 publish wheels for 3.11 to 3.13 only, so a machine whose
default python is newer needs the older one named explicitly."""
    if supported(sys.version_info[:2]) and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
        return sys.executable
    for minor in (13, 12, 11):
        if os.name == 'nt':
            code, out = capture(['py', f'-3.{minor}', '-c', 'import sys;print(sys.executable)'], timeout=30)
            if code == 0 and out.strip():
                return out.strip().splitlines()[-1]
        found = shutil.which(f'python3.{minor}') or shutil.which(f'python{minor}')
        if found and supported(python_version(found)):
            return found
    return sys.executable if supported(sys.version_info[:2]) else None


def missing_tools(env=None):
    return [name for name in tools.TOOLS if not shutil.which(name, path=(env or os.environ).get('PATH'))]


def record_tool_paths(found):
    """Put newly found tool folders on PATH now and into vault.config.json for later runs."""
    folders = sorted({str(f) for f in found.values()})
    tools.extend_path(folders)
    if folders and CONFIG.is_file():
        settings = read_config()
        existing = settings.get('tool_paths') or []
        settings['tool_paths'] = existing + [f for f in folders if f not in existing]
        write_config(settings)
    return folders


def install_windows_tools():
    """winget the three packages that are missing. Each raises one UAC prompt."""
    if not shutil.which('winget'):
        raise Stop('winget is not available. Install ffmpeg, ImageMagick and Tesseract by hand '
                   '(docs/setup.md lists them), then run this again.')
    for binary, package in tools.WINGET:
        if shutil.which(binary) or tools.locate(binary):
            continue
        print(f'    installing {package} for {binary} ({tools.TOOLS[binary]}). '
              'Windows will ask for permission once.', flush=True)
        command = ['winget', 'install', '--id', package, '-e', '--accept-package-agreements',
                   '--accept-source-agreements', '--silent']
        if stream(command) != 0 and not tools.locate(binary):
            # 0x800704c7 is a dismissed elevation prompt; the field report hit it once and a
            # second run succeeded. Anything else fails here with its own message.
            print(f'    {package} did not install. Trying once more; accept the permission prompt.', flush=True)
            if stream(command) != 0 and not tools.locate(binary):
                raise Stop(f'{package} would not install. Install it by hand from docs/setup.md, '
                           'then run this command again.')


def step_tools(report, args):
    started = report.begin('command-line tools', ', '.join(tools.TOOLS))
    record_tool_paths(tools.discover())
    absent = missing_tools()
    if not absent:
        report.end('tools', 'OK', 'ffmpeg, ffprobe, ImageMagick and Tesseract are all reachable', started)
        return
    if args.no_tools:
        raise Stop(f'missing: {", ".join(absent)}. Install them (docs/setup.md) and run this again.')
    if os.name == 'nt':
        install_windows_tools()
        record_tool_paths(tools.discover())
    else:
        chosen = tools.package_command()
        if chosen is None:
            raise Stop(f'missing: {", ".join(absent)}, and no known package manager. '
                       'Install them with your distribution\'s package manager, then run this again.')
        manager, command = chosen
        raise Stop(f'missing: {", ".join(absent)}. This one needs your password, so run it yourself:\n'
                   f'    {" ".join(command)}\nThen run this setup command again.')
    absent = missing_tools()
    if absent:
        raise Stop(f'still missing after installing: {", ".join(absent)}. Open a new terminal and run '
                   'this command again; some installers only reach a terminal opened afterwards.')
    report.end('tools', 'OK', 'installed and reachable: ' + ', '.join(tools.TOOLS), started)


def step_environment(report, args):
    started = report.begin('Python environment', f'{VENV}')
    if VENV_PYTHON.is_file() and capture([VENV_PYTHON, '-c', f'import {BASE_IMPORTS}'], timeout=180)[0] == 0:
        version = python_version(VENV_PYTHON)
        report.end('environment', 'SKIP', f'.venv already has the packages (Python {version[0]}.{version[1]})', started)
    else:
        if not VENV_PYTHON.is_file():
            base = base_interpreter()
            if base is None:
                raise Stop('no Python 3.11, 3.12 or 3.13 on this machine, and the wheels this project '
                           'needs are not published for anything newer. Install Python 3.13 '
                           + ('(winget install --id Python.Python.3.13)' if os.name == 'nt' else
                              'from your package manager') + ', then run this again.')
            print(f'    creating .venv from {base}', flush=True)
            if stream([base, '-m', 'venv', str(VENV)]) != 0 or not VENV_PYTHON.is_file():
                raise Stop(f'could not create a virtual environment at {VENV}')
        stream([VENV_PYTHON, '-m', 'pip', 'install', '--upgrade', 'pip', '--quiet'])
        if stream([VENV_PYTHON, '-m', 'pip', 'install', '-r', str(ROOT / 'requirements.txt')]) != 0:
            raise Stop('pip could not install requirements.txt. The output above says why.')
        code, out = capture([VENV_PYTHON, '-c', f'import {BASE_IMPORTS}'], timeout=180)
        if code:
            raise Stop('the packages installed but will not import:\n' + out.strip())
        report.end('environment', 'OK', 'requirements.txt installed into .venv', started)

    if not args.vision:
        return
    started = report.begin('vision packages', 'torch and transformers, several GB')
    if capture([VENV_PYTHON, '-c', 'import torch,transformers'], timeout=300)[0] == 0:
        report.end('vision packages', 'SKIP', 'torch and transformers are already installed', started)
    elif stream([VENV_PYTHON, '-m', 'pip', 'install', '-r', str(ROOT / 'requirements-vision.txt')]) != 0:
        raise Stop('pip could not install requirements-vision.txt. Run again without --vision to '
                   'finish the rest of the setup; image search can be added later.')
    else:
        report.end('vision packages', 'OK', 'torch and transformers installed', started)
    code, out = capture([VENV_PYTHON, '-c', 'import torch;print(torch.cuda.is_available())'], timeout=300)
    if code == 0 and 'True' not in out and shutil.which('nvidia-smi'):
        print('    note: this NVIDIA machine got the CPU build of torch. Image search will work but be '
              'slow. To swap in the CUDA build, follow the selector at https://pytorch.org/get-started/locally/',
              flush=True)


def read_config():
    if not CONFIG.is_file():
        return {}
    try:
        value = json.loads(CONFIG.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_config(settings):
    """Write the config with LF endings, because Python's text mode would make CRLF on Windows."""
    CONFIG.write_text(json.dumps(settings, indent=2) + '\n', encoding='utf-8', newline='\n')


def step_config(report, args):
    started = report.begin('configuration', CONFIG.name)
    settings = read_config()
    folders = []
    for entry in args.folders:
        path = Path(entry).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path)
        path = path.resolve()
        if not path.is_dir():
            raise Stop(f'that is not a folder: {path}')
        folders.append(str(path))
    if not folders:
        folders = settings.get('sources') or []
        if not folders:
            raise Stop('name the folder your photos and videos are in, for example:\n'
                       '    python scripts/setup.py ' + ('"D:\\Pictures"' if os.name == 'nt' else '~/Pictures'))
    state = Path(args.state_dir) if args.state_dir else None
    updated = {**CONFIG_DEFAULTS, **settings, 'sources': folders}
    updated['port'] = args.port or updated.get('port', 8770)
    if args.state_dir:
        updated['state_dir'] = str(state)
    if args.models_dir:
        updated['models_dir'] = str(Path(args.models_dir))
    write_config(updated)
    # Validation lives in one place; this reports the same message a later command would.
    code, out = capture([VENV_PYTHON if VENV_PYTHON.is_file() else sys.executable, '-c',
                         'import sys;sys.path.insert(0,%r);from src import config;config.load()' % str(ROOT)])
    if code:
        raise Stop('the config is not valid:\n' + out.strip())
    report.end('config', 'OK', f'{len(folders)} source folder(s), first {folders[0]}, port {updated["port"]}', started)
    return updated


def model_folder(settings):
    """The configured models folder, resolved the way src/config.py resolves it."""
    folder = Path(settings['models_dir']).expanduser()
    return folder if folder.is_absolute() else (ROOT / folder).resolve()


def step_models(report, args, settings):
    chosen = list(MODEL_SETS[args.models])
    if args.vision and '--all' not in chosen and '--vision' not in chosen:
        chosen.append('--vision')
    if not chosen:
        started = report.begin('models', 'none asked for')
        report.end('models', 'SKIP', 'no models, so faces, places and transcripts stay off', started)
        return
    sizes = ', '.join(MODEL_SIZES.get(flag, '') for flag in chosen)
    started = report.begin('models', f'{" ".join(chosen)} ({sizes} to download, once)')
    if stream([VENV_PYTHON, str(ROOT / 'scripts/setup_models.py'), '--models-dir', str(model_folder(settings)),
               *chosen]) != 0:
        raise Stop('a model download failed. Check the network and run this command again; '
                   'finished downloads are kept and verified, so it resumes where it stopped.')
    report.end('models', 'OK', 'downloaded and verified: ' + ' '.join(f.lstrip("-") for f in chosen), started)


def step_scan(report, args):
    started = report.begin('scan', 'reads every file once, hashes it, asks for its metadata')
    if stream([VENV_PYTHON, str(ROOT / 'vault.py'), 'scan']) != 0:
        raise Stop('the scan did not finish. The line above says why; a disconnected source drive and a '
                   'second scan already running are the usual reasons.')
    report.end('scan', 'OK', 'every file is inventoried and verified', started)


def step_enrich(report, args, settings):
    """Every optional stage the installed models allow, then place names, both to completion."""
    if args.no_enrich:
        for name in ('enrichment', 'places'):
            started = report.begin(name, 'skipped with --no-enrich')
            report.end(name, 'SKIP', 'run "python vault.py enrich --until-complete" when you want it', started)
        return
    started = report.begin('enrichment', 'faces, text in photos, similar shots, frames, transcripts')
    if stream([VENV_PYTHON, str(ROOT / 'vault.py'), 'enrich', '--until-complete']) != 0:
        raise Stop('enrichment stopped early. Run "python vault.py enrich --until-complete" again; '
                   'every stage keeps its own checkpoints and picks up where it left off.')
    report.end('enrichment', 'OK', 'every stage ran until nothing was left to process', started)

    gazetteer = model_folder(settings) / 'gazetteer'
    started = report.begin('places', 'GPS coordinates to place names, offline')
    if not gazetteer.is_dir():
        report.end('places', 'SKIP', 'no GeoNames snapshot; add one with setup-models --gazetteer', started)
        return
    if stream([VENV_PYTHON, str(ROOT / 'vault.py'), 'places']) != 0:
        raise Stop('labelling places failed. Run "python vault.py places" to see the error.')
    report.end('places', 'OK', 'coordinates labelled from the GeoNames snapshot', started)


def step_verify(report, args):
    started = report.begin('check', 'the doctor decides whether this is ready')
    code, out = capture([VENV_PYTHON, str(ROOT / 'scripts/doctor.py'), '--json'], timeout=120)
    try:
        facts = json.loads(out)
    except ValueError:
        raise Stop('the doctor did not report. Run "python vault.py doctor" to see what it says.')
    rows = {row['name']: row for row in facts['rows']}
    if not facts['ready']:
        broken = '; '.join(f'{r["name"]}: {r["detail"]}' for r in facts['rows'] if r['status'] == 'MISSING')
        raise Stop('setup ran but the doctor is not satisfied: ' + broken)
    report.end('check', 'OK', rows['state']['detail'], started)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='setup.py', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('folders', nargs='*', help='the folder(s) holding your photos and videos')
    parser.add_argument('--vision', action='store_true', help='also install torch and SigLIP2 image search')
    parser.add_argument('--models', choices=sorted(MODEL_SETS), default='standard')
    parser.add_argument('--no-tools', action='store_true', help='never install system packages')
    parser.add_argument('--no-enrich', action='store_true', help='stop after the scan')
    parser.add_argument('--serve', action='store_true', help='leave the viewer running at the end')
    parser.add_argument('--port', type=int)
    parser.add_argument('--state-dir')
    parser.add_argument('--models-dir')
    parser.add_argument('--repair-path', action='store_true',
                        help='only find installed tools PATH cannot see, record them, and exit')
    parser.add_argument('--json', action='store_true', dest='as_json')
    args = parser.parse_args(argv)

    if args.repair_path:
        found = tools.discover()
        folders = record_tool_paths(found)
        for binary, folder in sorted(found.items()):
            print(f'{binary}: {folder}')
        print(('recorded in vault.config.json: ' + ', '.join(folders)) if folders else
              ('every tool is already on PATH' if not missing_tools() else
               'these are still missing and were not found anywhere: ' + ', '.join(missing_tools())))
        return 0 if not missing_tools() else 1

    # tools, environment, [vision], config, models, scan, enrichment, places, check
    report = Report(9 if args.vision else 8, args.as_json)
    print(f'Media Vault setup on {platform.system()} {platform.release()}, repository {ROOT}')
    try:
        step_tools(report, args)
        step_environment(report, args)
        settings = step_config(report, args)
        step_models(report, args, settings)
        step_scan(report, args)
        step_enrich(report, args, settings)
        step_verify(report, args)
    except Stop as stop:
        print(f'\nSetup stopped: {stop}', file=sys.stderr)
        report.finish(False, str(stop))
        return 1
    except KeyboardInterrupt:
        print('\nSetup interrupted. Run the same command again to carry on where it stopped.', file=sys.stderr)
        report.finish(False, 'interrupted')
        return 130

    port = settings['port']
    url = f'http://127.0.0.1:{port}'
    summary = report.finish(True, url)
    print(f'\nReady. {summary["seconds"]}s in total.')
    print(f'  Open the viewer:     python vault.py        then open {url}')
    print('  Pick up new files:   python vault.py scan --watch')
    print('  Add more later:      python vault.py setup-models --vision   (image search by description)')
    print('  The one file to back up: corrections/organization.jsonl (your names, tags, buckets and trips)')
    if args.serve:
        print(f'\nStarting the viewer. Open {url}. Press Ctrl+C to stop it.', flush=True)
        return stream([VENV_PYTHON, str(ROOT / 'vault.py')])
    return 0


if __name__ == '__main__':
    sys.exit(main())
