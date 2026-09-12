#!/usr/bin/env python3
"""Media Vault launcher. Works the same on Windows and Linux; run it from the repo root.

    python vault.py                 start the viewer (http://127.0.0.1:8770 by default)
    python vault.py scan            inventory the sources, verify content, attach metadata
    python vault.py scan --watch    keep scanning for new files every 15 minutes
    python vault.py enrich          run the optional local AI stages for the models present
    python vault.py places          label places from the downloaded GeoNames snapshot
    python vault.py doctor          what is installed, what is configured, what fits this machine
    python vault.py setup-models    download optional models (--faces --whisper --vision --gazetteer)
    python vault.py test            unit tests

It re-executes itself with the project virtual environment's interpreter (.venv) when that
exists, so nobody has to remember to activate anything. Every command reads
vault.config.json; see vault.config.example.json.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
MODEL_FOLDERS = {'faces': 'face_models', 'whisper-small': 'transcript_model', 'siglip2': 'vision_model',
                 'qwen3-vl': 'description_model'}


def in_project_venv():
    return Path(sys.executable).resolve() == VENV_PYTHON.resolve()


def load_config():
    sys.path.insert(0, str(ROOT))
    from src import config
    try:
        value = config.load()
    except config.ConfigError as exc:
        print(f'vault.config.json: {exc}', file=sys.stderr)
        sys.exit(2)
    config.apply_environment(value)
    return value


def run(args, **kwargs):
    env = {**os.environ, 'PYTHONPATH': str(ROOT)}
    return subprocess.call([str(a) for a in args], cwd=ROOT, env=env, **kwargs)


def importable(*modules):
    for name in modules:
        code = subprocess.call([sys.executable, '-c', f'import {name}'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if code:
            return False
    return True


def scan(config, rest):
    """Run import cycles until the library is complete, or hand over to --watch."""
    sys.path.insert(0, str(ROOT))
    from src import jobs
    watch = '--watch' in rest
    passthrough = [a for a in rest if a != '--watch']
    exports = ['--exports', config['exports']] if config['exports'] else []
    if watch:
        return run([sys.executable, '-m', 'src.jobs', '--config', config['path'], '--directory', config['state_dir'], *exports, '--watch', *passthrough])
    seconds, limit = 300, 1000
    if '--seconds' in passthrough:
        seconds = int(passthrough[passthrough.index('--seconds') + 1])
    if '--limit' in passthrough:
        limit = int(passthrough[passthrough.index('--limit') + 1])
    cycle = 0
    while True:
        cycle += 1
        result = jobs.cycle(config['path'], config['state_dir'], config['exports'], seconds, limit)
        state = result.get('state')
        summary = {k: result.get(k) for k in ('state', 'master_files', 'metadata_pending') if k in result}
        if 'imports' in result:
            summary['verified_contents'] = result['imports'].get('verified_contents')
            summary['pending'] = result['imports'].get('pending')
        print(json.dumps({'cycle': cycle, **summary}), flush=True)
        if state == 'partial':
            continue
        if state == 'complete':
            print('Scan complete. Start the viewer with: python vault.py', flush=True)
            return 0
        if state == 'busy':
            print('Another scan owns this catalog; wait for it or stop it first.', file=sys.stderr)
            return 1
        if state == 'waiting_for_sources':
            print('A source folder is not available. Connect the drive or fix "sources" in vault.config.json.', file=sys.stderr)
            return 1
        print(json.dumps(result), file=sys.stderr)
        return 1


def serve(config, rest):
    state = config['state_dir']
    if not (state / 'catalog.db').is_file():
        print('No catalog yet. Run: python vault.py scan', file=sys.stderr)
        return 1
    args = [sys.executable, '-m', 'src.server', '--database', state / 'catalog.db', '--port', config['port']]
    if (state / 'imports.db').is_file():
        args += ['--imports', state / 'imports.db']
        vision = config['models_dir'] / 'siglip2'
        if vision.is_dir() and any(vision.glob('*.safetensors')):
            if importable('torch', 'transformers'):
                args += ['--vision-model', vision]
            else:
                print('models/siglip2 is present but torch/transformers are not installed; image search stays off (see requirements-vision.txt).', flush=True)
    else:
        print('Content verification has not finished, so people, places and search are off until a scan completes.', flush=True)
    return run(args + rest)


def resources_for(config):
    """The runtime and model paths enrichment may use, from what is actually installed."""
    resources = {}
    have_vision = importable('torch', 'transformers')
    for folder, key in MODEL_FOLDERS.items():
        path = config['models_dir'] / folder
        if not path.is_dir():
            continue
        if key == 'vision_model' and not any(path.glob('*.safetensors')):
            continue
        if key in ('vision_model', 'description_model') and not have_vision:
            continue
        resources[key] = str(path)
    resources['face_python'] = resources['audio_python'] = sys.executable
    if have_vision:
        resources['vision_python'] = sys.executable
    return resources


def enrich(config, rest):
    state = config['state_dir']
    if not (state / 'imports.db').is_file():
        print('No verified content yet. Run: python vault.py scan', file=sys.stderr)
        return 1
    resources = resources_for(config)
    state.mkdir(parents=True, exist_ok=True)
    path = state / 'resources.json'
    path.write_text(json.dumps(resources, indent=2))
    stages = [s for s, needs in __import__('src.enrichment', fromlist=['STAGE_NEEDS']).STAGE_NEEDS.items() if set(needs) <= set(resources)]
    print('Enrichment stages for this install: ' + ', '.join(stages), flush=True)
    sources = [arg for source in config['sources'] + ([config['exports']] if config['exports'] else []) for arg in ('--source', source)]
    return run([sys.executable, '-m', 'src.enrichment', '--resources', path, '--directory', state, *sources, *rest])


def places(config, rest):
    gazetteer = config['models_dir'] / 'gazetteer'
    state = config['state_dir']
    if not gazetteer.is_dir():
        print('No GeoNames snapshot. Run: python vault.py setup-models --gazetteer', file=sys.stderr)
        return 1
    if not (state / 'imports.db').is_file():
        print('No verified content yet. Run: python vault.py scan', file=sys.stderr)
        return 1
    return run([sys.executable, '-m', 'src.places', '--catalog', state / 'catalog.db', '--imports', state / 'imports.db',
                '--gazetteer', gazetteer, '--database', state / 'places.db', *rest])


def main(argv):
    if VENV_PYTHON.is_file() and not in_project_venv():
        return subprocess.call([str(VENV_PYTHON), str(ROOT / 'vault.py'), *argv], cwd=ROOT)
    command = argv[0] if argv and not argv[0].startswith('-') else 'serve'
    rest = argv[1:] if command != 'serve' else argv
    if command in ('-h', '--help', 'help'):
        print(__doc__)
        return 0
    if command == 'doctor':
        return run([sys.executable, ROOT / 'scripts/doctor.py', *rest])
    if command == 'setup-models':
        return run([sys.executable, ROOT / 'scripts/setup_models.py', *rest])
    if command == 'test':
        return run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', *rest])
    handlers = {'serve': serve, 'scan': scan, 'enrich': enrich, 'places': places}
    if command not in handlers:
        print(__doc__, file=sys.stderr)
        return 2
    sys.path.insert(0, str(ROOT))
    return handlers[command](load_config(), rest)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
