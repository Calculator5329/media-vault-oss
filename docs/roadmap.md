# Roadmap

Open work is a checkbox. Shipped work moves to `docs/changelog.md`.

## Now

- [x] First setup on Windows 11 through the agent path, with measurements. Done 2026-09-13: `docs/windows-setup-notes.md` is the field report, two crashes it found are fixed, and a 1,556-file library was scanned and enriched there.
- [ ] Run `python vault.py setup` itself on Windows 11, start to finish, on a machine that has none of the tools. The one-command path is built from that field report but has only been run on Linux, so the winget installs, the PATH repair and the venv creation are reasoned, not measured.
- [ ] First setup on Arch-based Linux on a machine that is not the author's, same evidence.
- [ ] A Windows service or scheduled-task equivalent of `src.services`, which today writes systemd units only. Until then Windows users run `python vault.py scan --watch` in a terminal.

## Next

- [ ] A one-command `setup-models --all` reference run with sizes and wall time per model in `docs/evidence/`, so people can decide what to download before they start.
- [ ] Descriptions (Qwen3-VL) as a downloadable model in `setup_models.py`. Today the code runs it if the folder exists, but the downloader only fetches faces, whisper, SigLIP2 and the gazetteer.
- [ ] Non-NVIDIA GPU notes for the vision runtime (ROCm, Apple silicon).

## Later

- [ ] An importer for a Google Takeout ZIP so people leaving Google Photos can bring their albums and edits along. The `exports` config key is the hook for it.
- [ ] Sharing a read-only album with someone on the same network, opt-in, still no cloud.
