# Optional local models

Media Vault catalogs, imports, deduplicates and browses with no models at all.
Each model below adds one search or grouping feature, and each is optional.
Download them with `scripts/setup_models.py`, which writes the `acquisition.json`
receipt every worker re-verifies before it loads anything.

```sh
python3 scripts/setup_models.py            # prints what each model is for, exits 2
python3 scripts/setup_models.py --faces --gazetteer
python3 scripts/setup_models.py --all      # about 2.1 GB downloaded
```

Files land in `models/<name>/` by default. Use `--models-dir` for another disk,
and `--force` to download again when a receipt already verifies.

## What is available

| Model | What it enables | Download | On disk | License | Runs on | Flag |
|---|---|---|---|---|---|---|
| YuNet + SFace | Detects faces and groups the same person across photos. Suggestions only, never a name. | 38.9 MB | 38.9 MB | Apache 2.0 (YuNet), MIT (SFace) | CPU | `--faces` |
| faster-whisper small | Speech in videos becomes searchable, timestamped text. | 486 MB | 486 MB | MIT (weights), model card on the repo | CPU | `--whisper` |
| SigLIP2 base patch16-224 | Search photos by describing them, and find visually similar images. | 1.54 GB | 1.54 GB | Apache 2.0 | CPU or GPU | `--vision` |
| GeoNames cities500 | Turns GPS coordinates into nearby place names, fully offline. | 13.8 MB | 13.8 MB | CC BY 4.0 | CPU, no model | `--gazetteer` |

Sizes are measured, not estimated. The gazetteer stays zipped on disk and is
read from the archive at load time.

## Pinned versions

Every download is pinned to an immutable revision, so two machines that run this
script get byte-identical weights and therefore identical model identities in the
catalog. Re-running the script after an upstream release does not silently change
your embeddings.

- Faces: `opencv/opencv_zoo` commit `47534e27c9851bb1128ccc0102f1145e27f23f98`.
  The ONNX weights live in Git LFS, so the script pulls them from
  `media.githubusercontent.com`, not `raw.githubusercontent.com`, which serves a
  131-byte pointer file instead of the model.
- Whisper: `Systran/faster-whisper-small` revision `536b0662742c02347bc0e980a01041f333bce120`.
- Vision: `google/siglip2-base-patch16-224` revision `75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2`.
- Gazetteer: GeoNames publishes a moving file set with no revision to pin, so the
  receipt records the digests of whatever that install received, plus the date.

Neither Hugging Face repository is gated as of 2026-09-12. If one becomes gated,
the script says so and stops. Accept the terms on huggingface.co, create a read
token, and set `HF_TOKEN` in the environment before re-running. The script sends
that token only to huggingface.co and never prints it.

## Which machine should install what

**No GPU, 8 GB RAM.** Faces, OCR, fingerprints and transcripts are all
comfortable. The face models are tiny, Whisper small runs int8 on four CPU
threads, and OCR and fingerprints use no downloaded model at all. Install
`--faces --whisper --gazetteer` and expect no trouble.

**No GPU, and you want image search.** SigLIP2 runs correctly on CPU and search
queries stay fast, because a query embeds one short string. The slow part is the
one-time indexing pass over your library. Per-image CPU indexing time is
**unmeasured** on this fork, so plan a long first pass and run the indexer in the
background rather than trusting a number nobody took. Indexing resumes from its
checkpoint, so it does not need to finish in one sitting.

**With a GPU.** SigLIP2 moves to CUDA automatically when `torch.cuda.is_available()`,
with no flag to set. The two models below become available.

## The two models this script does not download

Qwen3-VL descriptions and MUSIQ quality scoring are GPU-only, large, and not
pinned by this repo, so you place them by hand.

**MUSIQ** needs no receipt. Put `musiq_koniq_ckpt-e95806b9.pth` in a folder and
point `--model` at it. `src/quality.py` checks the file against a hardcoded
SHA-256 (`e95806b9eae5...`) and refuses to start without CUDA.

**Qwen3-VL** needs an `acquisition.json` beside the weights, in the same shape
the Whisper receipt uses. `src/descriptions.py` hashes every listed file and
refuses to load on any mismatch:

```json
{
  "repo": "<the Qwen3-VL repository you downloaded>",
  "revision": "<the commit sha you pinned>",
  "files": [
    {"file": "config.json", "sha256": "..."},
    {"file": "model.safetensors", "sha256": "..."}
  ]
}
```

List every file the loader reads, including the processor and tokenizer files.
The revision and the file list feed the description model identity, so changing
either one correctly invalidates previously generated descriptions rather than
mixing two models' output in one catalog.

## The offline guarantee

The download is the only time this software talks to a model host. Afterwards:

- Every worker sets `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` and
  `HF_HUB_DISABLE_TELEMETRY=1` before importing a machine learning library, and
  loads its folder with `local_files_only=True`. A missing file is an error, not
  a silent re-download.
- No image bytes, video bytes, transcript text or embedding ever leaves the
  machine. Inference is local, and the workers make no outbound request at all.
- Each worker re-hashes its model files at load time against the receipt. If a
  file changed, the worker refuses to run rather than writing facts that claim to
  come from a model that is no longer there.
- Model identity is a digest of the receipt plus the library versions and the
  settings used. Facts in the catalog carry that identity, so output from two
  different model versions can never be confused for one another.

You can verify all of this by pulling the network cable after the download and
running any worker.
