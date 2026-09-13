# Changelog

## 2026-09-13, later

One command sets the whole thing up, and the Windows field report's rough edges are gone.

- `python vault.py setup "D:\Pictures"` runs the whole path: command-line tools, `.venv`,
  `vault.config.json`, the small models, the scan, enrichment to completion, place names, and
  the doctor's verdict with the viewer URL. Any Python 3 starts it. Every step measures before
  it acts, so a second run after a failure, a reboot or a Ctrl+C carries on rather than
  starting over. `--vision`, `--models`, `--no-enrich`, `--serve` and `--json` are the flags.
  On Windows it installs the three winget packages itself; on Linux it prints the one
  `pacman`/`apt`/`dnf` line that needs a password and stops.
- `tool_paths` in `vault.config.json`: folders put on PATH for every command the vault runs.
  `python vault.py setup --repair-path` finds a tool an installer left off PATH (the
  UB-Mannheim Tesseract package always does this) and records it. The system PATH is not
  touched.
- `enrich --until-complete` repeats every stage until its backlog is empty and then exits,
  and stops a stage that no longer shrinks its backlog. `--once` was one bounded pass per
  stage, which on a first run left most of the library behind.
- The doctor reports what each enrichment stage has left to process, names the folder to add
  when a tool is installed but off PATH, and no longer warns about a missing `identify` that
  `src/probe.py` already handles by calling `magick identify`.
- Tests that need a symbolic link, a case-sensitive filesystem or systemd now skip themselves
  where the platform cannot do it, measured at run time rather than assumed from the OS name.
  `.gitattributes` keeps the tree LF so a Windows clone cannot commit CRLF.
- Verified: 265 unit tests, and a fresh clone on Arch Linux taken from nothing to a served
  catalog by the one command in 39 seconds, then re-run to 4 seconds with everything already
  in place. The Windows path is built from the field report in `docs/windows-setup-notes.md`
  and has not been run on Windows yet.

## 2026-09-13

Faces in videos.

- The face pass runs over the retained video samples as well as photos, so naming a face puts
  the person's videos in their results. One face per distinct person per video, strongest
  detection kept. The detail panel shows each video face with the time it was seen, and the
  face chip is cropped from the sample rather than decoded from the video.
- `face_work` gained a `kind` column, added in place on existing stores, so a file that was
  scanned as a photo and later verified as a video is redone from its samples. Photo face
  identities are unchanged, so existing names carry over.
- People, untagged and candidate counts include videos; the tag and select controls accept
  videos.
- Verified: 264 unit tests (two new), and a face pass over a 1,556-file library on Windows 11.

## 2026-09-12, later

The viewer and its server code now come from the private vault's current UI branch, not the
older main branch the first cut was copied from.

- One search box with suggestions and result groups replaces the toolbar of date pickers,
  slideshow, highlights and filter chips. Year and month scrubber on the timeline.
- Map view (offline Natural Earth), trips with journey legs on the map, buckets, similar
  shots, fill-the-gaps proposals, favorite and hide per photo, face strips inside cards,
  export of buckets to folders, playback that streams originals and encodes in place.
- The viewer now lists every configured source, not only the first: catalogs under
  `.catalog/source-catalogs/` are read alongside `catalog.db`.
- Similar-shot hashing runs in the base environment (it needs no torch), so it is on for
  every install.
- Gone with the older branch: MUSIQ quality scoring, the additive-sources page under
  Imports, and the recovered-image provenance stages that only made sense for the author's
  damaged archive.
- Verified: 262 unit tests, a scan of the CC0 test library (56 files across two sources),
  faces, OCR and similar-shot passes, place labels, and the screenshots on the README page.

## 2026-09-12

Code-only public fork of the author's private Media Vault, prepared for distribution.

- History starts fresh; no personal catalog, corrections or evidence came along.
- `vault.py` launcher for both Windows and Linux: `scan`, `serve` (default), `enrich`,
  `places`, `doctor`, `setup-models`, `test`. Re-executes itself in `.venv`.
- Configuration moved into `vault.config.json`: sources (flat folders or a root folder with
  any hierarchy under it), optional exports folder, state and models folders, port.
- Portability layer (`src/portable.py`): file locks use `fcntl` on POSIX and `msvcrt` on
  Windows; the "derived state must stay on the local drive" guard reads its removable-media
  roots from config instead of hard-coding `/run/media`.
- Enrichment runs whichever stages have both their runtime and their model present, instead
  of refusing unless every model is installed.
- Exports folder is optional everywhere (inventory, metadata, jobs).
- `scripts/doctor.py` measures Python, packages, binaries, config, models and GPU and prints
  `READY` when a scan can run. `scripts/setup_models.py` downloads pinned models with
  checksum receipts.
- ImageMagick 7 without the `identify` shim (`magick identify`) and tessdata in the usual
  places on Windows, Debian, Arch and Homebrew.
- The local-index-kit provenance and rebuild modules are vendored under `src/localindexkit`.
- Verified on the author's CachyOS machine: 266 unit tests in a fresh venv, a scan of a
  synthetic flat folder plus a three-level hierarchy (10 files, one duplicate, one video),
  viewer served on port 8771.
