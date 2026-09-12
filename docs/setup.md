# Setup

The agent path in `CLAUDE.md` runs these same steps. This page is for doing it by hand, or
for checking what the agent did.

## 1. Command-line tools

Media Vault shells out to four programs and treats everything they print as data.

| Tool | Used for |
|---|---|
| `ffprobe` | video metadata, dates, durations |
| `ffmpeg` | preview frames, audio for transcripts |
| ImageMagick (`identify` or `magick identify`) | image dimensions, EXIF, GPS |
| Tesseract with `eng.traineddata` | text in photos (optional stage) |

**CachyOS / Arch**

```bash
sudo pacman -S --needed ffmpeg imagemagick tesseract tesseract-data-eng python
```

**Debian / Ubuntu**

```bash
sudo apt install ffmpeg imagemagick tesseract-ocr tesseract-ocr-eng python3-venv
```

**Windows 11** (PowerShell; `winget` ships with Windows)

```powershell
winget install --id Python.Python.3.13 --id Gyan.FFmpeg --id ImageMagick.ImageMagick --id UB-Mannheim.TesseractOCR
```

Open a new terminal afterwards so PATH updates. ImageMagick 7 on Windows has no `identify`
shim; the code calls `magick identify` when `identify` is absent. Tesseract installs to
`C:\Program Files\Tesseract-OCR`; add that folder to PATH if `tesseract --version` fails.
The tessdata folder is found automatically there and in the usual Linux and Homebrew
locations; set `TESSDATA_PREFIX` if yours is somewhere else.

## 2. Python environment

Python 3.11, 3.12 or 3.13. From the repo root:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

On Windows:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

`vault.py` finds `.venv` on its own, so you never need to activate it.

`requirements.txt` is the CPU set: Pillow, numpy, OpenCV (faces), onnxruntime (faces),
faster-whisper (transcripts), av (video frames). Image search and descriptions need torch
and transformers from `requirements-vision.txt`; install those only if you want them.
On an NVIDIA machine use the CUDA build of torch from pytorch.org, otherwise the default
CPU build works and is slow.

## 3. Configuration

```bash
cp vault.config.example.json vault.config.json
```

```json
{
  "sources": ["/absolute/path/to/your/photos"],
  "exports": null,
  "tier": "personal",
  "state_dir": ".catalog",
  "models_dir": "models",
  "port": 8770,
  "external_roots": null
}
```

- `sources`: one or more folders. Flat, or a root with any hierarchy below it. Windows paths
  are fine (`"D:\\Pictures"` or `"D:/Pictures"`).
- `exports`: an unpacked Google Takeout folder, if you have one. Otherwise `null`.
- `state_dir`, `models_dir`: where derived state and models go. Relative paths are relative to
  the repo. They must be on a local drive and must not be inside a source.
- `external_roots`: where removable drives mount. `null` means `/mnt`, `/run/media` and
  `/media` on Linux and nothing on Windows. Derived state is refused under these roots so an
  unplugged drive never takes the catalog with it.

## 4. Doctor, scan, serve

```bash
python vault.py doctor
python vault.py scan
python vault.py
```

`doctor` prints one line per check and `READY` when a scan can run. `scan` runs until every
file is inventoried, hashed and probed, printing a JSON line per cycle. `python vault.py`
serves http://127.0.0.1:8770.

New files: `python vault.py scan --watch` keeps scanning every few minutes. On Linux,
`python -m src.services` writes systemd user units that do this on login. There is no
Windows service yet; keep the watch command in a terminal.

## 5. Optional models

```bash
python vault.py setup-models --faces --gazetteer      # people and places, 53 MB
python vault.py setup-models --whisper                 # video transcripts, 486 MB
python vault.py setup-models --vision                  # image search, 1.5 GB, needs torch
python vault.py enrich --once
python vault.py places
```

`docs/models.md` lists every model, its source, license and checksum. `enrich` runs only the
stages whose runtime and model are both present and says which at start. `enrich` with no
flags keeps running and picks up new files; `--once` does one pass.

## Troubleshooting

- `vault.config.json: ...` on any command: the config failed validation; the message names
  the key.
- `Derived state must stay on the local drive`: `state_dir` or `models_dir` resolves under a
  removable-media root. Move it or set `external_roots`.
- `Another scan owns this catalog`: a `scan --watch` or a service is already running.
- Tesseract found but OCR stage missing: run `tesseract --list-langs`; `eng` must be listed.
- Windows: if `python` opens the Microsoft Store, use `py` instead.
