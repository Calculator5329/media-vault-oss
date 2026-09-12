"""Synthetic media for the test suite, built with ffmpeg at run time.

No binary fixtures are checked in. Everything here is generated from ffmpeg's
own synthetic sources, so the corpus is reproducible on any machine with the
same tools and the repo stays free of committed media.

The corpus is built once per process and copied per test, because generating
it costs several seconds and every test wants its own mutable copy.

Two fixture choices are load-bearing rather than arbitrary:

- The images are gradients and test patterns, never flat colour fields. A
  flat image reduces to 64 identical grey samples, its average hash is decided
  by rounding noise, and two unrelated flat images would match perfectly.
  Testing variant grouping on flat fixtures would prove nothing.
- `exif_d.jpg` carries a real EXIF block, assembled byte by byte below.
  ffmpeg cannot write one and this machine has no exiftool, so without this
  the EXIF path would ship untested against a real file.
"""

import atexit
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

# What the injected EXIF block says, so tests can assert against one source.
EXIF_DATETIME = "2026:07:04 10:20:30"
EXIF_DATETIME_ISO = "2026-07-04T10:20:30"
EXIF_LAT_DMS = (41, 52, 48.0)
EXIF_LON_DMS = (87, 37, 12.0)
EXIF_LAT = 41.88
EXIF_LON = -87.62

PHOTOS = ("grad_a.jpg", "grad_a_small.jpg", "test_b.jpg", "bars_c.jpg",
          "exif_d.jpg")
VIDEO = "clip.mp4"
# bars_c.jpg copied byte for byte under another name, in a subfolder.
DUPLICATE_OF_BARS = "copies/bars_c_again.jpg"
# grad_a.jpg re-encoded at half size: same picture, different bytes.
VARIANT_PAIR = ("grad_a.jpg", "grad_a_small.jpg")
OTHER_FILE = "notes/notes.txt"


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


# ------------------------------------------------------------------ EXIF


def _ifd(entries, base, next_offset=0):
    """One little-endian TIFF IFD: (tag, type, count, payload) entries."""
    body = struct.pack("<H", len(entries))
    data_offset = base + 2 + len(entries) * 12 + 4
    tail = b""
    for tag, typ, count, payload in entries:
        if payload is None or len(payload) <= 4:
            value = (payload or b"").ljust(4, b"\0")
        else:
            value = struct.pack("<I", data_offset + len(tail))
            tail += payload
            if len(payload) % 2:
                tail += b"\0"
        body += struct.pack("<HHI", tag, typ, count) + value
    return body + struct.pack("<I", next_offset) + tail


def build_exif_segment(datetime_text=EXIF_DATETIME, lat=EXIF_LAT_DMS,
                       lon=EXIF_LON_DMS, lat_ref=b"N\0", lon_ref=b"W\0"):
    """A complete APP1/Exif segment with DateTimeOriginal and a GPS IFD."""

    def rationals(values):
        out = b""
        for value in values:
            out += struct.pack("<II", int(round(value * 100)), 100)
        return out

    tiff_base = 8
    ifd0_size = 2 + 2 * 12 + 4
    exif_offset = tiff_base + ifd0_size
    stamp = datetime_text.encode() + b"\0"
    exif_size = 2 + 1 * 12 + 4 + len(stamp) + (len(stamp) % 2)
    gps_offset = exif_offset + exif_size

    ifd0 = _ifd(
        [
            (0x8769, 4, 1, struct.pack("<I", exif_offset)),
            (0x8825, 4, 1, struct.pack("<I", gps_offset)),
        ],
        tiff_base,
    )
    exif_ifd = _ifd([(0x9003, 2, len(stamp), stamp)], exif_offset)
    gps_ifd = _ifd(
        [
            (0x0001, 2, 2, lat_ref),
            (0x0002, 5, 3, rationals(lat)),
            (0x0003, 2, 2, lon_ref),
            (0x0004, 5, 3, rationals(lon)),
        ],
        gps_offset,
    )
    assert len(ifd0) == ifd0_size
    assert len(exif_ifd) == exif_size
    tiff = b"II" + struct.pack("<HI", 42, tiff_base) + ifd0 + exif_ifd + gps_ifd
    payload = b"Exif\0\0" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def inject_exif(source, destination, **kwargs):
    raw = Path(source).read_bytes()
    assert raw[:2] == b"\xff\xd8", "not a JPEG"
    Path(destination).write_bytes(
        raw[:2] + build_exif_segment(**kwargs) + raw[2:]
    )


# ---------------------------------------------------------------- corpus


def build_corpus(destination):
    """Write the fixture corpus into `destination` and return the path."""
    root = Path(destination)
    (root / "copies").mkdir(parents=True, exist_ok=True)
    (root / "notes").mkdir(parents=True, exist_ok=True)

    # A left-to-right black-to-white gradient. Seed and corner coordinates are
    # pinned because unseeded `gradients` picks a random direction per run: it
    # produced three different images in three runs, which would have made
    # variant grouping intermittently wrong. The horizontal black/white ramp
    # is also the best separated of the deterministic patterns tried, at
    # Hamming distance 35 or more from every other fixture against a grouping
    # threshold of 8, so the test cannot pass or fail on a thin margin.
    _ffmpeg("-f", "lavfi", "-i",
            "gradients=s=640x480:c0=black:c1=white:nb_colors=2:seed=7"
            ":x0=0:y0=240:x1=639:y1=240:d=1",
            "-frames:v", "1", str(root / "grad_a.jpg"))
    # The same picture at half size: the near-variant case.
    _ffmpeg("-i", str(root / "grad_a.jpg"), "-vf", "scale=320:240",
            str(root / "grad_a_small.jpg"))
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=640x480:d=1",
            "-frames:v", "1", str(root / "test_b.jpg"))
    _ffmpeg("-f", "lavfi", "-i", "smptebars=s=320x240:d=1",
            "-frames:v", "1", str(root / "bars_c.jpg"))
    # Byte-for-byte copy under a different name: the exact-duplicate case.
    shutil.copy2(root / "bars_c.jpg", root / DUPLICATE_OF_BARS)
    _ffmpeg("-f", "lavfi", "-i", "mandelbrot=s=400x300",
            "-frames:v", "1", str(root / "_mandel.jpg"))
    inject_exif(root / "_mandel.jpg", root / "exif_d.jpg")
    (root / "_mandel.jpg").unlink()
    _ffmpeg("-f", "lavfi", "-i", "testsrc=s=320x240:d=1:r=15",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-t", "1", "-pix_fmt", "yuv420p", str(root / VIDEO))

    (root / OTHER_FILE).write_text("not media, and not meant to be\n")
    (root / "README.md").write_text("skipped by the indexer\n")
    return root


_master = None


def corpus():
    """The shared master corpus, built on first use."""
    global _master
    if _master is None:
        tmp = tempfile.TemporaryDirectory(prefix="media-vault-fixtures-")
        atexit.register(tmp.cleanup)
        _master = build_corpus(Path(tmp.name))
    return _master


def fresh_corpus(destination):
    """A private copy of the corpus, bytes and mtimes preserved."""
    shutil.copytree(corpus(), destination, dirs_exist_ok=True)
    return Path(destination)


def replace_with_new_image(path):
    """Overwrite a fixture with a different image, for edit-detection tests."""
    _ffmpeg("-f", "lavfi", "-i", "rgbtestsrc=s=640x480:d=1",
            "-frames:v", "1", str(path))
    return Path(path)
