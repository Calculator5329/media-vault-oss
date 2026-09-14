# Media Vault: instructions for the agent setting this up

You are reading this because someone cloned Media Vault and asked you to set it up for their
photos. Media Vault catalogs the photos and videos already on this computer and serves a
private viewer on localhost. Nothing is uploaded anywhere. The originals are read-only to
this program. Everything it derives is rebuildable and lives under `.catalog/`.

Your job is to get `python vault.py doctor` to print `READY`, run the first scan to
completion, and hand over a viewer URL that shows their photos. `python vault.py setup
<their photo folder>` does all of that in one command; the rest of this file is what to ask
first, what to do when a step stops, and the lines you must not cross.

## The procedure

**Measure, offer, run.** Ask the person which folder holds their photos and videos, then
print the offer for this machine:

```
python vault.py setup --options "D:\Pictures"
```

It reads the GPU, RAM and free disk and prints three things: what works with no model at
all, what the default download adds (people, places, transcripts, text; about 511 MB), and
the extras, with `--vision` marked recommended when an NVIDIA GPU is present. Read that back
to the person in a few lines, in this shape:

> Out of the box you get the timeline, dates with their evidence, duplicates across folders,
> search, buckets, trips, the offline map and the quality audit. The default setup adds
> people, places and video transcripts (511 MB of models). This machine has an RTX 4070, so
> I'd also add image search by description ("dog on a beach"), about 4 GB. Want that, or
> just the default?

Always offer; never silently include or skip `--vision`. If they say "whatever you
recommend", run the `Suggested command` line the offer printed. Then run it from the
repository root:

```
python vault.py setup "D:\Pictures" --vision
```

Any Python 3 starts it; it builds the right environment itself. It installs the
command-line tools, creates `.venv`, writes `vault.config.json`, downloads the models,
scans the library, runs enrichment until every stage is empty, labels places and ends with
the doctor's verdict and the viewer URL. It prints a numbered step per phase, skips anything
already done, and is safe to run again after a failure, a reboot, or a Ctrl+C: every step
measures before it acts.

The flags, all optional and all listed by `--options`:

- `--vision` also installs torch and SigLIP2 so photos can be searched by description.
  About 4 GB on disk; fast on an NVIDIA GPU, hours on a CPU for a large library.
- `--models none` skips the model downloads entirely (catalog, timeline and search still work).
- `--no-enrich` stops after the scan, for a quick first look at a huge library.
- `--serve` leaves the viewer running at the end instead of just printing the URL.
- `--json` adds one JSON line per step (and one JSON document for `--options`), which is
  the easiest thing for you to report from.

A long library scan and the enrichment pass both take real time. Run the command in the
background and report progress from its output rather than waiting silently.

### When a step stops

The command stops at the first step it cannot finish and prints what to do. Two cases
need you rather than a retry:

- **Linux command-line tools.** Installing them needs a password, so setup prints the exact
  `pacman`/`apt`/`dnf` line and stops. Show the person that line, let them run it, then run
  setup again.
- **Windows, tool installed but not found.** An installer that never touched PATH is the
  usual cause. `python vault.py setup --repair-path` looks in the places those installers
  use, records what it finds in `vault.config.json` under `tool_paths`, and every later
  command picks it up. No system PATH is changed.

Everything else is worth one retry before you dig: model downloads resume, the scan and
every enrichment stage keep their own checkpoints.

### Doing it step by step instead

If the person wants to see each piece, or setup fails somewhere you need to work around,
`docs/setup.md` has every command by hand and `docs/models.md` covers the models. The
useful individual commands are `python vault.py doctor` (measure first, it reports Python,
the venv, packages, the four tools, the config, enrichment progress and GPU memory),
`scan`, `enrich --until-complete`, `places`, `setup-models`, and `test`.

### Handing over

Start `python vault.py`, confirm `http://127.0.0.1:8770/api/summary` reports the number of
files the person expects, and tell them:

- the URL, and that `python vault.py` is how they start it again;
- `python vault.py scan --watch` to keep picking up new files;
- the one file to back up: `corrections/organization.jsonl`, which holds every name, tag,
  bucket and trip they created. `.catalog/` and `models/` are rebuildable; that file is not.

## Rules while you do this

- Never write into a source folder. Not a thumbnail, not a sidecar, not a lock file. The
  code refuses to put derived state there and you must not work around it.
- Image and video bytes never leave this machine. No cloud vision APIs, no uploads "to check
  something", no pasting a photo into a chat. Model downloads are the only network step.
- Do not run anything with elevated privileges without saying what it installs and why.
  Package installs are the only steps that may need it.
- Do not change ports, firewall rules, or other services. The server binds 127.0.0.1 only.
- On Windows, write any file you edit with `newline='\n'` or in binary mode. Python's text
  mode turns every `\n` into `\r\n`, which turns a three-line fix into a whole-file diff.
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
