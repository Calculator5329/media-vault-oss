# Roadmap

Open work is a checkbox. Shipped work moves to `docs/changelog.md`.

## Now

- [x] <!-- workspace:id=work:6a5b229c-3974-525c-9dbb-664151583645 --> First setup on Windows 11 through the agent path, with measurements. Done 2026-09-13: `docs/windows-setup-notes.md` is the field report, two crashes it found are fixed, and a 1,556-file library was scanned and enriched there.
- [ ] <!-- workspace:id=work:a915eeab-c230-5472-8c00-d17e6fc7d8f0 --> Run `python vault.py setup` itself on Windows 11, start to finish, on a machine that has none of the tools. The one-command path is built from that field report but has only been run on Linux, so the winget installs, the PATH repair and the venv creation are reasoned, not measured.
- [ ] <!-- workspace:id=work:27eac93b-77ab-5016-8a7b-0948736910f0 --> First setup on Arch-based Linux on a machine that is not the author's, same evidence.
- [ ] <!-- workspace:id=work:e1d3e7b4-99bb-53a9-9a40-dcfb722baadc --> A Windows service or scheduled-task equivalent of `src.services`, which today writes systemd units only. Until then Windows users run `python vault.py scan --watch` in a terminal.

## Next

- [ ] <!-- workspace:id=work:f9644611-034b-5423-9f31-12cbc5e152e4 --> A one-command `setup-models --all` reference run with sizes and wall time per model in `docs/evidence/`, so people can decide what to download before they start.
- [ ] <!-- workspace:id=work:6f804966-971a-5dc1-8250-4da7f053cdf8 --> Descriptions (Qwen3-VL) as a downloadable model in `setup_models.py`. Today the code runs it if the folder exists, but the downloader only fetches faces, whisper, SigLIP2 and the gazetteer.
- [ ] <!-- workspace:id=work:1b428db8-5e23-598f-a164-c68dcce9a79b --> Non-NVIDIA GPU notes for the vision runtime (ROCm, Apple silicon).

## Later

- [ ] <!-- workspace:id=work:80819389-5464-56c2-a789-d1b2ed70168c --> An importer for a Google Takeout ZIP so people leaving Google Photos can bring their albums and edits along. The `exports` config key is the hook for it.
- [ ] <!-- workspace:id=work:67ba18f0-cfea-5351-afdc-a0a17d8f7497 --> Sharing a read-only album with someone on the same network, opt-in, still no cloud.
- [ ] <!-- workspace:id=work:18e04250-2fa3-5d3e-b898-f53396e2be42 --> Adding a folder to `sources` after the first scan stops with "Database belongs to different sources" (`src/imports.py`). Setup should be able to add a source and carry the existing catalog forward instead of asking for a rebuild.
- [ ] <!-- workspace:id=work:f19ce5db-efd6-5b8b-aa96-f0865cd060f4 --> Real raw files in the fixture corpus (a DNG at least) so the libraw path in ImageMagick is measured, not assumed; today raws are inventoried and paired by name only.
