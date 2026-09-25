# Changelog

## 2026-09-24 (README demo, install check, CI)

- `docs/demo.gif` and `docs/demo.mp4`: the timeline and two searches, recorded with Playwright
  against 26 generated sample pictures. No real media.
- README section "How an agent installs this", with the real output of `python vault.py setup`
  run in a fresh clone. It finished in 74.8 s with no failed step.
- `.github/workflows/test.yml` runs `python vault.py test` on push and pull request, with a
  badge in the README. It has not run on GitHub yet.
- A Thanks line for @ambalene314.

## 2026-09-14 (detail panel)

The panel on the right of an open photo or video is rebuilt so a person can read it.

- Facts read as sentences: `Aug 25, 2026, 7:45 PM UTC` with the source underneath in
  plain words (`Google Photos record, time taken`, `Camera (EXIF), original capture time`)
  and a `timezone unknown` note where that applies. A `Change` link opens the capture-date
  choice; the choice is a radio list, one card per source, and only appears on its own when
  the sources disagree or a date is missing. Keywords, caption, title and rating are shown
  when a file carries them.
- Location on disk is one row: a folder icon that reveals the file in the system file
  manager (`POST /api/reveal`, loopback only, the file must be in the catalog), the path
  shortened with an ellipsis in the middle and the full path on hover, and a copy icon.
- Favorite is a star whose button keeps its size in both states; Hide sits beside it.
  Tiles show the star too.
- Add to bucket is a checklist dropdown under a bucket icon: tick a bucket to add, untick
  to remove, `New bucket…` at the bottom. Chips for the buckets a file is in stay under it.
- Similar shots: the panel shows the stack as a thumbnail strip, marks which one the grid
  shows, and offers `Shown in the grid`, `Separate all N` and `Open as a list`. Clicking a
  thumbnail opens that shot in place instead of leaving for a filtered grid.
- Transcript is laid out like a video-site transcript: time on the left, text on the right,
  each row seeks the video.
- AI description shows the caption and one chip per distinct object. The model repeats
  an object once per instance and miscounts, so nine `black crutch` entries collapse to
  one chip; new descriptions are deduplicated when they are written.
- Detected text hides words the reader scored under 0.5 or that are not at least two
  letters or digits, says how many fragments were hidden, and keeps the full read under
  `Where each fact came from`.
- `Where each fact came from` replaces the raw dump: one card per source (Google Photos
  record, camera EXIF, sidecar, AI description, detected text, files) with labelled rows,
  the raw record behind a fold, and the JSON behind another.
- The dropdown focus ring is a single accent border; the sort and year menus no longer
  show a double highlight.
- The `Media Vault` mark is a button back to the photo grid, and a search from any view
  runs on the photo grid instead of the view it was typed in. `Saved highlights` is a
  button in the Buckets toolbar.

## 2026-09-14

Stacks are always collapsed, the Similar view is gone, and metadata comes from more places.

- Google Takeout sidecars are read when they sit beside the photos, not only inside the zip:
  `IMG.jpg.json`, `.supplemental-metadata.json`, Takeout's truncated names and its `(1)`
  placement. One sidecar to one file or nothing; ambiguous and orphaned ones are counted.
- XMP and IPTC, embedded or in a `.xmp` sidecar: keywords (`dc:subject`, Lightroom's
  hierarchical subjects, IPTC 2:25), caption, title, rating, and `photoshop:DateCreated` or
  `xmp:CreateDate` as the capture date when EXIF has none. Keywords and captions are
  searchable and shown in the detail panel, labelled as written by an editing tool. A
  sidecar's fields win over the embedded packet, field by field. `src/embedded.py`.
- PNG `Creation Time` gives screenshots a date.
- Videos: Apple's `com.apple.quicktime.creationdate` (local time with its offset) is read
  before the UTC `creation_time`, and an old camera's `date` tag after it. Offsets written
  without a colon parse.
- Camera raw files (DNG, NEF, CR2, CR3, ARW, ORF, RW2, RAF, PEF and more) are inventoried
  as photos instead of skipped. A raw beside the JPEG with the same stem stacks under it,
  so the grid shows the JPEG and the badge fans out to the raw; Separate splits them.

- Every near-duplicate or burst stack collapses to its top in the grid, dated or not. The
  `⧉ 3` badge fans the stack out in place: click a shot to make it the top, Separate to keep
  the shots apart, Open to browse the stack alone. The detail panel offers the same two
  actions. There is no longer a proposed/confirmed distinction to learn, no review queue and
  no "Similar" entry in the rail; the "Similar stacks" filter remains for sweeping them.
  Choosing a top still records the stack in `corrections/organization.jsonl` and Separate
  still records the verdict, so nothing changed in the file format.

## 2026-09-13, later

One command sets the whole thing up, and the Windows field report's rough edges are gone.

- `python vault.py setup --options "D:\Pictures"` prints the offer for this machine: what
  works with no model, what the default 511 MB download adds, and the extras, with `--vision`
  marked recommended when `nvidia-smi` reports a GPU, plus a suggested command line.
  `CLAUDE.md` now has the agent read that offer back and ask before running setup, so image
  search is always offered on a GPU machine and never silently added or skipped.
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
