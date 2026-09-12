# Media Vault: instructions for the agent setting this up

You are reading this because someone cloned Media Vault and asked you to set it up for their
photos. Media Vault catalogs the photos and videos already on this computer and serves a
private viewer on localhost. Nothing is uploaded anywhere. The originals are read-only to
this program. Everything it derives is rebuildable and lives under `.catalog/`.

Your job is to get `python vault.py doctor` to print `READY`, run the first scan to
completion, and hand over a viewer URL that shows their photos. Read `docs/setup.md` for the
platform commands and `docs/models.md` for the optional models.

## The procedure

1. **Measure first.** Run `python vault.py doctor` (any Python 3 works for this first run).
   It reports Python, the venv, packages, the four command-line tools, the config, which
   models are present, and GPU memory. Every later step fixes one `MISSING` line.
2. **Command-line tools.** `ffmpeg`, `ffprobe`, ImageMagick (`magick` or `identify`) and
   Tesseract with English data. `docs/setup.md` has the `pacman`, `apt` and `winget` lines.
   Say what each package is before installing it.
3. **Python 3.11 to 3.13 in `.venv`** at the repo root, then `pip install -r requirements.txt`.
   Do not install `requirements-vision.txt` (torch) unless the person wants image search or
   descriptions and the doctor reports a GPU or they accept CPU speed; it is several
   gigabytes.
4. **Configure.** Copy `vault.config.example.json` to `vault.config.json`. Ask the person
   one question: which folder or folders hold their photos. Put absolute paths in `sources`.
   A folder may be flat or a root with any hierarchy beneath it. Leave `state_dir` and
   `models_dir` alone unless the repo sits on a small or removable drive; the catalog must be
   on a local disk, and the doctor will say so if it is not.
5. **Scan.** `python vault.py scan`. It prints one JSON line per cycle and ends with
   "Scan complete". A large library takes a while: roughly a few hundred files a minute for
   the metadata pass, because every file is hashed and probed once. Run it in the
   background and report progress from its output rather than waiting silently.
6. **Verify.** `python vault.py doctor` prints `READY`. `python vault.py test` passes
   (unit tests, under a minute). Start `python vault.py` in the background, fetch
   `http://127.0.0.1:8770/api/summary`, and confirm `files` matches what they expect. Then
   stop it.
7. **Optional models.** Offer, do not assume: `python vault.py setup-models --faces`
   (39 MB, people), `--gazetteer` (14 MB, places), `--whisper` (486 MB, video
   transcripts), `--vision` (1.5 GB plus torch, image search). Tell them the size before
   each download. After any download run `python vault.py enrich --once` and, for places,
   `python vault.py places`.
8. **Hand over.** Start `python vault.py` and tell the person to open http://127.0.0.1:8770.
   Give them the restart command, the `scan --watch` command for new files, and the one file
   to back up: `corrections/organization.jsonl`.

## Rules while you do this

- Never write into a source folder. Not a thumbnail, not a sidecar, not a lock file. The
  code refuses to put derived state there and you must not work around it.
- Image and video bytes never leave this machine. No cloud vision APIs, no uploads "to check
  something", no pasting a photo into a chat. Model downloads are the only network step.
- Do not run anything with elevated privileges without saying what it installs and why.
  Package installs are the only steps that may need it.
- Do not change ports, firewall rules, or other services. The server binds 127.0.0.1 only.
- Do not modify files under `src/`, `web/` or `tests/` to make setup pass. Configuration
  belongs in `vault.config.json`. If the code needs a change for this machine, say so and
  stop; that is a bug report, not a local patch.
- Do not delete `corrections/organization.jsonl` or anything the person named in the viewer.
  `.catalog/` and `models/` are disposable; corrections are not.
- Report measurements as measurements. "The scan verified 4,212 files in 9 minutes" is a
  fact; "it should be fast" is not. If a step could not be verified, say that.
- If the machine has no NVIDIA GPU, say plainly that image search and descriptions will be
  slow or unavailable, and still finish the base setup. The catalog, people, places, text
  and transcripts run on the CPU.

## What Media Vault deliberately does not do

No cloud sync, no sharing links, no editing of originals, no account. The viewer is for one
person on one machine; anything else is a plug-in point for later, not a gap to fill during
setup.
