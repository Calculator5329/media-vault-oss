# Media Vault

A private photo and video library that runs on your own computer. Point it at the folders
your pictures already live in, and it builds a catalog next to them: a timeline, people,
places, trips, albums, duplicates, and search, in a browser page served on localhost. Your
originals are never moved, renamed or written to. No image ever leaves the machine.

Optional local AI stages add faces, text-in-photos, image search by description, video
transcripts and scene previews. Every one of them is a model you download once and run
offline; skip any of them and the rest of the vault works the same.

## Set it up with an agent

Clone the repo and open it in Claude Code (or any coding agent that reads `CLAUDE.md` or
`AGENTS.md`), then say:

> Set up Media Vault for the photos in this folder.

The agent runs the doctor, installs the three command-line tools it needs for your OS,
creates the Python environment, writes the config with your folder, runs the first scan, and
hands you the URL. Windows 11 and Arch-based Linux (CachyOS) are the tested targets; other
Linux distributions differ only in the package manager commands.

## Set it up by hand

`docs/setup.md` has the commands. The short version:

1. `ffmpeg`, `ffprobe`, ImageMagick and Tesseract on your PATH.
2. Python 3.11 to 3.13 in `.venv`, then `pip install -r requirements.txt`.
3. `cp vault.config.example.json vault.config.json`, set `sources` to your folders.
4. `python vault.py doctor` until it prints `READY`.
5. `python vault.py scan`, then `python vault.py` and open http://127.0.0.1:8770.

## What sources look like

`sources` is a list of folders. Each can be a flat folder of files or the root of any
hierarchy (`Photos/2023/summer/...`, camera dumps, phone backups); every image and video
under it is found. Two copies of the same photo in different folders are recorded once
with both locations. A folder on a drive that is unplugged is reported, not forgotten,
and the scan resumes when it returns.

Everything the vault derives lives in `.catalog/` inside the repo (or wherever `state_dir`
points) and can be deleted and rebuilt at any time. The one file worth backing up is
`corrections/organization.jsonl`: the names, tags, albums and date fixes you enter in the
viewer, keyed by content hash so they survive a rebuild.

## What it does

- **Timeline.** Dates from EXIF and video containers, with the source of each date shown.
  Files with no date get their own section rather than a guessed one.
- **Duplicates and versions.** SHA-256 identity across every source; near-duplicate and
  edited-version grouping once the fingerprint stage has run.
- **People.** Faces detected and grouped locally (OpenCV YuNet and SFace); you name a group
  and the name is a correction, not a model output.
- **Places and trips.** GPS from EXIF resolved against an offline GeoNames snapshot; trips
  inferred from runs of days away from home.
- **Search.** Filenames, dates, text found in photos (Tesseract), spoken words in videos
  (faster-whisper), and, with the SigLIP2 weights installed, plain-language image search
  ("dog on a beach") computed on your GPU or CPU.
- **Albums, highlights, slideshow, archive review.** All in the page; all local.
- **Nothing phones home.** The server binds 127.0.0.1. The only network use is the one-time
  model downloads you ask for with `setup-models`.

## Commands

```
python vault.py                 start the viewer
python vault.py scan            inventory, verify, attach metadata (repeat until "complete")
python vault.py scan --watch    keep watching for new files
python vault.py enrich          run whichever AI stages have their models installed
python vault.py places          resolve GPS to place names (needs the gazetteer)
python vault.py doctor          what is installed, what is configured, what fits this machine
python vault.py setup-models    download optional models
python vault.py test            unit tests
```

## Layout

```
vault.py                   launcher; re-executes itself inside .venv
vault.config.json          your sources, folders and port (copy the .example)
src/catalog.py             inventory and ImageMagick / ffprobe metadata
src/imports.py             content identity across sources
src/enrichment.py          supervisor for the optional AI stages
src/server.py              the localhost viewer and its API
src/portable.py            file locks and the removable-media guard, per OS
src/localindexkit/         vendored provenance and rebuild rules for derived facts
web/                       the page
scripts/doctor.py          the checklist an agent or a person runs
scripts/setup_models.py    pinned model downloads with checksum receipts
docs/                      setup, models, roadmap, changelog
```

## License

MIT. The optional models carry their own licenses; `docs/models.md` lists each one.
