"""``vault.config.json``: where the originals are and where derived state may live.

Shape (all paths absolute, or relative to the repository root):

    {
      "sources": ["D:/Photos"],            at least one folder; flat or a whole tree
      "exports": null,                      optional folder of Google Takeout ZIP files
      "tier": "personal",
      "state_dir": ".catalog",              catalogs, previews, worker logs
      "models_dir": "models",               optional local AI models
      "port": 8770,
      "external_roots": null,               mount roots treated as removable, see portable.py
      "tool_paths": null                    folders prepended to PATH so ffmpeg and friends are found
    }

Unknown keys are an error so a typo never silently means "default".
"""
import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = REPO_ROOT / 'vault.config.json'
DEFAULTS = {'sources': [], 'exports': None, 'tier': 'personal', 'state_dir': '.catalog',
            'models_dir': 'models', 'port': 8770, 'external_roots': None, 'tool_paths': None}


class ConfigError(ValueError):
    pass


def _folder(value, key):
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f'{key}: expected a folder path')
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def load(path=None):
    path = Path(path) if path else DEFAULT_PATH
    if not path.is_file():
        raise ConfigError(f'{path.name} not found; copy vault.config.example.json to {path.name} and set "sources"')
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise ConfigError(f'{path.name} is not valid JSON: {exc}') from exc
    if not isinstance(raw, dict):
        raise ConfigError(f'{path.name} must hold a JSON object')
    unknown = set(raw) - set(DEFAULTS)
    if unknown:
        raise ConfigError('unknown keys in ' + path.name + ': ' + ', '.join(sorted(unknown)))
    value = {**DEFAULTS, **raw}
    if not isinstance(value['sources'], list) or not value['sources']:
        raise ConfigError('"sources" must list at least one folder')
    sources = [_folder(s, 'sources') for s in value['sources']]
    if len(set(sources)) != len(sources):
        raise ConfigError('"sources" repeats a folder')
    for source in sources:
        if not source.is_dir():
            raise ConfigError(f'source is not a folder that exists: {source}')
    exports = _folder(value['exports'], 'exports') if value['exports'] else None
    if value['tier'] != 'personal':
        raise ConfigError('"tier" must be "personal"')
    if not isinstance(value['port'], int) or value['port'] not in range(1024, 65536):
        raise ConfigError('"port" must be an integer between 1024 and 65535')
    roots = value['external_roots']
    if roots is not None and (not isinstance(roots, list) or not all(isinstance(r, str) for r in roots)):
        raise ConfigError('"external_roots" must be a list of folder paths')
    tools = value['tool_paths']
    if tools is not None and (not isinstance(tools, list) or not all(isinstance(t, str) and t.strip() for t in tools)):
        raise ConfigError('"tool_paths" must be a list of folder paths')
    state_dir = _folder(value['state_dir'], 'state_dir')
    models_dir = _folder(value['models_dir'], 'models_dir')
    for key, folder in (('state_dir', state_dir), ('models_dir', models_dir)):
        for source in sources + ([exports] if exports else []):
            if folder == source or source in folder.parents:
                raise ConfigError(f'"{key}" cannot be inside a source folder ({source})')
    return {'path': path, 'sources': sources, 'exports': exports, 'tier': 'personal', 'state_dir': state_dir,
            'models_dir': models_dir, 'port': value['port'], 'external_roots': roots,
            'tool_paths': [str(_folder(t, 'tool_paths')) for t in tools] if tools else []}


def apply_environment(config, env=None):
    """Export the removable-root list and put the configured tool folders on PATH.

Every worker inherits this environment, so a Windows install where the Tesseract or
ImageMagick installer never touched PATH still finds them. Folders already on PATH are
left where they are, so a system install always wins over a recorded one."""
    env = os.environ if env is None else env
    if config['external_roots'] is not None:
        env['MEDIA_VAULT_EXTERNAL_ROOTS'] = os.pathsep.join(config['external_roots'])
    existing = [p for p in env.get('PATH', '').split(os.pathsep) if p]
    known = {os.path.normcase(p) for p in existing}
    extra = [p for p in config.get('tool_paths', []) if os.path.normcase(p) not in known]
    if extra:
        env['PATH'] = os.pathsep.join(extra + existing)
    return env
