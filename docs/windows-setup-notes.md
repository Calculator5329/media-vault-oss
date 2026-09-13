# Windows 11 setup notes: a field report

What it actually took to get Media Vault running on a Windows 11 machine, following
`CLAUDE.md` with a coding agent. Written as we went so the rough edges can become an
upstream PR later. Everything below is a measurement or a thing we had to do; nothing is
a guess.

## The machine

| | |
|---|---|
| OS | Windows 11 Home 10.0.26200 |
| CPU / RAM | 8 threads, 15.8 GB |
| GPU | NVIDIA GeForce GTX 1050 Ti, 4 GB VRAM |
| Python | 3.13.1 (also 3.10 on PATH, which the doctor correctly ignored) |
| Repo location | first `C:\Users\<user>\OneDrive\Desktop`, later moved to `F:\projects` (exFAT external Seagate) |
| Library | `F:\2025`, 1,560 files, 40.8 GB, 1,186 photos and 374 videos |

## What we did, in order

1. `python scripts/doctor.py` with system Python. Reported Python OK and everything else MISSING.
2. Installed tools with winget: `Gyan.FFmpeg` 9.0.1, `ImageMagick.ImageMagick` 7.1.2, `UB-Mannheim.TesseractOCR` 5.4.0.
3. `py -3.13 -m venv .venv` and `pip install -r requirements.txt`.
4. Wrote `vault.config.json` with the source folder.
5. `vault.py scan`, then `vault.py`. The viewer crashed. See bug 1.
6. Models: `setup-models --faces`, then `--whisper --gazetteer`, then CUDA torch and `--vision`.
7. `vault.py enrich --once` in a loop until every stage reported zero remaining.
8. `vault.py places`. Crashed. See bug 2.
9. Two more rounds of adding files to the source folder: scan, enrich loop, places.

## Bugs found and fixed locally

Both are committed separately on top of the upstream snapshot so they can be sent as a PR.

### 1. Viewer crashes on start: `preexec_fn is not supported on Windows platforms`

`src/previews.py`, `Warmer.start()`. The preview warmer spawns its worker with
`preexec_fn=lambda: os.nice(15)`. Python's `subprocess` raises `ValueError` for
`preexec_fn` on Windows, so the viewer printed its URL and died. Fix: on Windows pass
`creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS` instead, keep `os.nice` elsewhere.

### 2. `vault.py places` crashes: `UnicodeDecodeError: 'charmap' codec can't decode byte 0x9d`

`src/places.py`, `Gazetteer.__init__`. Three `Path.read_text()` calls on the GeoNames
files had no `encoding`, so Windows used cp1252. The `cities500.zip` reader a few lines
below already passed `encoding='utf-8'`. Fix: pass `encoding='utf-8'` on all three.

## Things that needed a workaround but were not code bugs

### Tesseract is not on PATH after install

The UB-Mannheim installer does not add `C:\Program Files\Tesseract-OCR` to PATH. We
added it to the user PATH. `docs/setup.md` already says this; it would be worth the
doctor printing the exact folder to add when it finds the binary at the default
location but not on PATH.

### winget install can be cancelled by the UAC prompt

The first Tesseract install ended with `0x800704c7: The operation was canceled by the user`
because the elevation prompt was dismissed. Re-running the same command succeeded. Worth a
note in `docs/setup.md`: each of the three installs raises one UAC prompt.

### `vault.py doctor` warns about `identify` even though the code handles it

The doctor prints `WARN imagemagick: only magick at ...; src/probe.py calls identify, so
the launcher needs an identify shim`. `src/probe.py` already falls back to `magick identify`
when `identify` is absent, and everything worked. The warning text is stale.

### Loading SigLIP2 failed once with `OSError 1455: The paging file is too small`

Happened while trying to load the model in a test process at the same time as the face,
OCR, frame and transcript workers were all running. Free RAM was 4.5 GB of 15.8, page
file 6 GB auto-managed. The enrichment worker itself, launched by the supervisor a few
minutes later, loaded and indexed all photos with zero errors, and the viewer loaded the
model fine once the workers were done. So: transient memory pressure, not a config
problem. Worth a line in `docs/models.md`: on a 16 GB Windows machine, do not start a
second torch process while enrichment is running.

### transformers 5.x prints config warnings for SigLIP2

`requirements-vision.txt` allows `transformers<6`. pip resolved 5.17.0, which logs
`bos_token_id must be None or an integer within the vocabulary ... got 49406` twice at
load. Search results were correct. Harmless, but noisy.

### Python text-mode writes turn the LF repo into CRLF

The repository stores LF line endings. Any Python that edits a source file with
`open(path, 'w')` on Windows writes CRLF, because text mode translates `\n` on write.
Our first two fix commits were made that way and came out as whole-file diffs (every
line changed) even though each fix touched three lines. We rebuilt the commits from
LF content and set `git config core.autocrlf input` in the clone so git normalises on
commit. An agent editing files on Windows should write with `newline='\n'` or in
binary mode. Upstream could add a `.gitattributes` with `* text=auto eol=lf` so a
Windows contributor cannot ship CRLF by accident.

### Git on an exFAT external drive

exFAT records no file ownership, so every git command fails with
`fatal: detected dubious ownership` until you run
`git config --global --add safe.directory <path to your clone>`.

### Moving the repo folder with Explorer left the dot-folders behind

Dragging the project folder from Desktop to `F:` moved the visible files but left
`.git`, `.gitignore` and `.venv` at the old path. We copied `.git` over by hand. The
venv copy worked from the new path with no changes: `pyvenv.cfg` records the base
interpreter, not the venv location, and `vault.py` finds `.venv` relative to itself.

### Removable-media guard does not apply on Windows

`src/portable.py` has `_DEFAULT_ROOTS = ()` on Windows, so the doctor accepted
`.catalog` and `models` on an external USB drive without comment. On Linux the same
setup would have been refused. That is fine for us, but the README's promise that
"derived state is refused on removable media" is Linux-only today.

## Unit tests on Windows

`python vault.py test`: 262 tests, 12 failed, 1 skipped, 48 seconds. All 12 are
platform assumptions, not app bugs:

| Cause | Tests |
|---|---|
| Creating symlinks needs admin on Windows | `test_catalog`, `test_enrichment`, `test_server` symlink tests |
| Renders Linux systemd units and expects `/run/media` paths | `test_services`, `test_ingest` config tests |
| Case-insensitive filesystem breaks "case twin" folder test | `test_export` |
| Expects POSIX path or example-config behaviour | `test_ingest`, `test_organization`, `test_server` preview cache |

We did not touch the tests. A PR could mark these `skipIf(os.name == 'nt')` or make
them platform-aware.

## Measurements

| Step | Result |
|---|---|
| First scan, 1,107 files, 38.7 GB, USB drive | about 8 minutes, 0 errors |
| Face pass, 748 photos, CPU | about 4 minutes, 647 faces |
| OCR, Tesseract, CPU | about 105 photos per 10-minute pass |
| Whisper small, int8 CPU, 2.3 hours of video | about 105 videos per 10-minute pass |
| SigLIP2 on the 1050 Ti | 748 photos and 1,731 keyframes in one pass, 0 errors |
| Incremental scan, 49 new photos | 5 seconds, one enrichment pass |
| Incremental scan, 404 new files | 22 seconds, four enrichment passes (OCR is the long pole) |
| `.catalog` after full enrichment of 1,556 files | 1.3 GB |
| `models` with all four models | 2.0 GB |
| `.venv` with CUDA torch | 12 GB on disk as reported by exFAT |

## Suggestions for the upstream PR

- Land the two fixes above.
- `enrich --once` returns after one bounded pass per stage; OCR and transcripts needed
  seven passes on the first run. A `--until-complete` flag, or a note that `--once` means
  "one bounded pass" rather than "finish", would save confusion.
- The doctor could print `processed`/`remaining` per stage so people know when enrichment
  is actually done.
- The People page labels everything "photos" (`N of M photos`, `photos without people
  tags`). Fine today because faces run only on photos, but see the video faces work.
