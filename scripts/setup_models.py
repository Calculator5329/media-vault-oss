#!/usr/bin/env python3
"""Download the optional local models and write the acquisition receipts.

Every worker in this repo refuses to load a model folder whose acquisition.json
does not match the bytes on disk, so downloading and writing the receipt are one
operation. Nothing here talks to a model provider at run time: after a download
the workers run with HF_HUB_OFFLINE=1 and read only these folders.

Standard library only. Python 3.11 to 3.13.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request

REPO_ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = 'media-vault-setup'
CHUNK = 1024 * 1024

# opencv_zoo keeps the ONNX weights in Git LFS, so raw.githubusercontent.com
# serves a 131-byte pointer file rather than the model. The media host resolves
# LFS content for the same commit.
ZOO_COMMIT = '47534e27c9851bb1128ccc0102f1145e27f23f98'
ZOO_MEDIA = f'https://media.githubusercontent.com/media/opencv/opencv_zoo/{ZOO_COMMIT}/models'
ZOO_RAW = f'https://raw.githubusercontent.com/opencv/opencv_zoo/{ZOO_COMMIT}/models'

WHISPER_REPO = 'Systran/faster-whisper-small'
WHISPER_REVISION = '536b0662742c02347bc0e980a01041f333bce120'

VISION_REPO = 'google/siglip2-base-patch16-224'
VISION_REVISION = '75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2'

GEONAMES = 'https://download.geonames.org/export/dump'
GEONAMES_ATTRIBUTION = (
    'GeoNames gazetteer data, https://www.geonames.org/, used under the '
    'Creative Commons Attribution 4.0 license (CC BY 4.0).'
)

# Pinned digests. The revisions above are immutable, so these are the exact
# bytes every install receives, which keeps the model identity a worker derives
# byte-identical across machines. GeoNames publishes a moving file set and has
# no pinnable revision, so the gazetteer records what it received instead.
FACE_FILES = [
    {'file': 'face_detection_yunet_2023mar.onnx',
     'url': f'{ZOO_MEDIA}/face_detection_yunet/face_detection_yunet_2023mar.onnx',
     'sha256': '8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4'},
    {'file': 'face_recognition_sface_2021dec.onnx',
     'url': f'{ZOO_MEDIA}/face_recognition_sface/face_recognition_sface_2021dec.onnx',
     'sha256': '0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79'},
]
FACE_LICENSES = [
    {'file': 'face_detection_yunet-LICENSE',
     'url': f'{ZOO_RAW}/face_detection_yunet/LICENSE'},
    {'file': 'face_recognition_sface-LICENSE',
     'url': f'{ZOO_RAW}/face_recognition_sface/LICENSE'},
]

# faster-whisper loads the folder with local_files_only=True. README.md is kept
# because the receipt's file list feeds the worker's identity digest.
WHISPER_FILES = [
    {'file': 'README.md', 'sha256': '329373481008c7c38654aff8ecdcf0163c211557cc7ba8e2ef6f2f84b4f75ec8'},
    {'file': 'config.json', 'sha256': 'b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828'},
    {'file': 'model.bin', 'sha256': '3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671'},
    {'file': 'tokenizer.json', 'sha256': 'fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab'},
    {'file': 'vocabulary.txt', 'sha256': '34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913'},
]

# src/vision.py digests every .json, .safetensors, .model and .txt file in the
# folder, so adding or omitting one changes every embedding identity. This is
# the exact set, and no more.
VISION_FILES = [
    {'file': 'config.json', 'sha256': 'fe8b5fe6d5734360678fd71c11c21e1ea3364bd8598d34295d9206335973ffd7'},
    {'file': 'model.safetensors', 'sha256': '612923381c76ec5a9bed335d1c48827e3f2e506ac31b044b63b2031fadee6a0b'},
    {'file': 'preprocessor_config.json', 'sha256': '9b36b57ebaf20f09bf4c22100ccc21877ea6bfe5aead0c00c59f8af8ccefacfc'},
    {'file': 'special_tokens_map.json', 'sha256': 'baec30ea10906f16adb8c18af7a34023002c1746542612b8b41c9f09e1351351'},
    {'file': 'tokenizer.json', 'sha256': 'cb9140fae3ac5122c972d37adf83e1248471a38147ad76f8215c8872c6fd8322'},
    {'file': 'tokenizer.model', 'sha256': '61a7b147390c64585d6c3543dd6fc636906c9af3865a5548f27f31aee1d4c8e2'},
    {'file': 'tokenizer_config.json', 'sha256': '14afe629fe4959b9e0d51e1852b8d9f7ad074f90a1a7125a4fcdd17f06e78fc8'},
]

GAZETTEER_FILES = ['cities500.zip', 'admin1CodesASCII.txt', 'countryInfo.txt']

PURPOSE = {
    'faces': 'faces: YuNet detection plus SFace embeddings, groups the same person across photos. CPU, about 37 MB.',
    'whisper': 'whisper: faster-whisper small, speech in videos becomes searchable text. CPU, about 461 MB.',
    'vision': 'vision: SigLIP2 base, search photos by describing them. CPU works, a GPU is much faster. About 1.4 GB.',
    'gazetteer': 'gazetteer: GeoNames cities500, turns GPS coordinates into place names offline. No model, about 13 MB.',
}


class SetupError(Exception):
    """A failure worth reporting as one plain sentence."""


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def open_url(url, method='GET', extra_headers=None):
    headers = {'User-Agent': USER_AGENT}
    if extra_headers:
        headers.update(extra_headers)
    token = os.environ.get('HF_TOKEN')
    if token and urllib.parse.urlsplit(url).hostname == 'huggingface.co':
        headers['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request(url, headers=headers, method=method)
    return urllib.request.urlopen(request, timeout=120)


def download(url, destination, expected=None):
    """Stream one file to destination, hashing as it goes. Returns (bytes, sha256)."""
    partial = destination.with_name(destination.name + '.partial')
    digest = hashlib.sha256()
    written = 0
    try:
        with open_url(url) as response:
            header = response.headers.get('Content-Length')
            total = int(header) if header and header.isdigit() else None
            with partial.open('wb') as stream:
                while chunk := response.read(CHUNK):
                    stream.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                    progress(destination.name, written, total)
        progress(destination.name, written, total, final=True)
    except urllib.error.HTTPError as error:
        partial.unlink(missing_ok=True)
        raise SetupError(f'Download of {destination.name} failed with HTTP {error.code} from {url}.') from None
    except OSError as error:
        partial.unlink(missing_ok=True)
        raise SetupError(f'Download of {destination.name} from {url} failed: {error}.') from None
    actual = digest.hexdigest()
    if expected and actual != expected:
        partial.unlink(missing_ok=True)
        raise SetupError(
            f'{destination.name} does not match its pinned checksum, so it was discarded. '
            f'Expected {expected}, received {actual}.')
    partial.replace(destination)
    return written, actual


def progress(name, written, total, final=False):
    if total:
        line = f'  {name}: {written}/{total} bytes ({written * 100 // total}%)'
    else:
        line = f'  {name}: {written} bytes'
    sys.stderr.write('\r' + line.ljust(78) + ('\n' if final else ''))
    sys.stderr.flush()


def receipt_entries(receipt):
    """The file list a receipt carries, whichever key this model uses."""
    return receipt.get('models', []) + receipt.get('files', []) + receipt.get('licenses', [])


def verifies(folder):
    """True when the folder holds a receipt whose files are all present and unchanged."""
    path = folder / 'acquisition.json'
    try:
        receipt = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    entries = receipt_entries(receipt)
    if not entries:
        return False
    for entry in entries:
        target = folder / entry['file']
        expected = entry.get('sha256')
        if not expected:
            continue
        try:
            with target.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                    return False
        except OSError:
            return False
    return True


def write_receipt(folder, receipt):
    """Write the receipt last and atomically, so a failed run leaves none."""
    partial = folder / 'acquisition.json.partial'
    partial.write_text(json.dumps(receipt, indent=2) + '\n')
    partial.replace(folder / 'acquisition.json')


def fetch_all(folder, items):
    """Download every item, returning receipt rows. Raises before any receipt is written."""
    rows = []
    for item in items:
        written, actual = download(item['url'], folder / item['file'], item.get('sha256'))
        rows.append({'file': item['file'], 'bytes': written, 'sha256': actual})
    return rows


def install_faces(folder):
    models = fetch_all(folder, FACE_FILES)
    licenses = fetch_all(folder, FACE_LICENSES)
    write_receipt(folder, {
        'upstream': 'opencv/opencv_zoo',
        'revision': ZOO_COMMIT,
        'at': now(),
        'models': models,
        'licenses': licenses,
    })
    return models + licenses


def install_huggingface(folder, repo, revision, files):
    items = [{'file': f['file'], 'sha256': f['sha256'],
              'url': f'https://huggingface.co/{repo}/resolve/{revision}/{f["file"]}'}
             for f in files]
    rows = fetch_all(folder, items)
    write_receipt(folder, {'repo': repo, 'revision': revision, 'at': now(), 'files': rows})
    return rows


def install_gazetteer(folder):
    items = [{'file': name, 'url': f'{GEONAMES}/{name}'} for name in GAZETTEER_FILES]
    rows = fetch_all(folder, items)
    write_receipt(folder, {
        'source': GEONAMES,
        'at': now(),
        'attribution': GEONAMES_ATTRIBUTION,
        'pinned': False,
        'note': 'GeoNames republishes these files, so the receipt records what this install received.',
        'files': rows,
    })
    return rows


INSTALLERS = {
    'faces': ('faces', install_faces),
    'whisper': ('whisper-small', lambda folder: install_huggingface(folder, WHISPER_REPO, WHISPER_REVISION, WHISPER_FILES)),
    'vision': ('siglip2', lambda folder: install_huggingface(folder, VISION_REPO, VISION_REVISION, VISION_FILES)),
    'gazetteer': ('gazetteer', install_gazetteer),
}


def check_gated(repo):
    """Say so plainly when a Hugging Face repo has become gated since this was pinned."""
    try:
        with open_url(f'https://huggingface.co/api/models/{repo}') as response:
            gated = json.load(response).get('gated')
    except (OSError, ValueError):
        return
    if gated:
        raise SetupError(
            f'The {repo} repository is now gated ({gated}). Accept its terms on huggingface.co, '
            'create a read token, and run this again with HF_TOKEN set in the environment.')


def install(name, models_dir, force):
    folder_name, installer = INSTALLERS[name]
    folder = models_dir / folder_name
    if not force and verifies(folder):
        print(f'ok {name} (already present)')
        return
    folder.mkdir(parents=True, exist_ok=True)
    if name in ('whisper', 'vision'):
        check_gated(WHISPER_REPO if name == 'whisper' else VISION_REPO)
    # Drop any prior receipt first: a run that fails partway must never leave a
    # receipt that claims files it did not finish writing.
    (folder / 'acquisition.json').unlink(missing_ok=True)
    print(f'downloading {name} into {folder}')
    rows = installer(folder)
    print(json.dumps({
        'name': name,
        'folder': str(folder),
        'bytes': sum(r['bytes'] for r in rows),
        'files': [r['file'] for r in rows],
    }))


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='setup_models.py',
        description='Download the optional local models and write their acquisition receipts.',
        epilog='Models are optional. The catalog, import and search work without any of them.')
    parser.add_argument('--faces', action='store_true', help=PURPOSE['faces'])
    parser.add_argument('--whisper', action='store_true', help=PURPOSE['whisper'])
    parser.add_argument('--vision', action='store_true', help=PURPOSE['vision'])
    parser.add_argument('--gazetteer', action='store_true', help=PURPOSE['gazetteer'])
    parser.add_argument('--all', action='store_true', help='Install all four.')
    parser.add_argument('--models-dir', default=str(REPO_ROOT / 'models'),
                        help='Where model folders live. Default: %(default)s')
    parser.add_argument('--force', action='store_true',
                        help='Download again even when the existing receipt verifies.')
    args = parser.parse_args(argv)

    selected = [name for name in INSTALLERS if args.all or getattr(args, name)]
    if not selected:
        parser.print_usage()
        print('\nPick at least one model. Each is optional and each enables one feature:\n')
        for name in INSTALLERS:
            print('  --' + name.ljust(10) + PURPOSE[name])
        print('\n  --all       Install all four, about 1.9 GB downloaded.')
        print('\nAfter a download the workers read these folders offline and send nothing anywhere.')
        return 2

    models_dir = Path(args.models_dir).expanduser().resolve()
    for name in selected:
        try:
            install(name, models_dir, args.force)
        except SetupError as error:
            print(str(error), file=sys.stderr)
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
