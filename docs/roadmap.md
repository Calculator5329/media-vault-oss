# Roadmap

Open work is a checkbox. Shipped work moves to `docs/changelog.md`.

## Now

- [ ] First setup by a stranger on Windows 11 through the agent path, with the doctor output attached to the report. Until then Windows is documented, not proven: the file locks and process groups have Windows branches, but nobody has run them there.
- [ ] First setup on Arch-based Linux on a machine that is not the author's, same evidence.
- [ ] A Windows service or scheduled-task equivalent of `src.services`, which today writes systemd units only. Until then Windows users run `python vault.py scan --watch` in a terminal.

## Next

- [ ] A one-command `setup-models --all` reference run with sizes and wall time per model in `docs/evidence/`, so people can decide what to download before they start.
- [ ] Descriptions (Qwen3-VL) and quality (MUSIQ) as downloadable models in `setup_models.py`. Today the code runs them if the folders exist, but the downloader only fetches faces, whisper, SigLIP2 and the gazetteer.
- [ ] Non-NVIDIA GPU notes for the vision runtime (ROCm, Apple silicon).

## Later

- [ ] An importer for a Google Takeout ZIP so people leaving Google Photos can bring their albums and edits along. The `exports` config key is the hook for it.
- [ ] Sharing a read-only album with someone on the same network, opt-in, still no cloud.
