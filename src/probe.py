"""Shelling out to ImageMagick and ffmpeg, and parsing what they say.

This machine has no PIL and no exiftool, so metadata comes from `identify`
and `ffprobe`. Everything these tools print is treated as data: parsed
defensively, never evaluated, never trusted to be present. A tool that fails
on one file yields no facts for that file rather than aborting the ingest,
because one unreadable download should not stop a vault from indexing.

Kept apart from ingest.py so the "talk to tools" seam can be exercised
directly by tests without walking a directory or opening a database.
"""

from datetime import datetime
import json
import re
import subprocess

TOOL_TIMEOUT_S = 120

PHOTO_EXTS = frozenset(
    ".jpg .jpeg .png .gif .bmp .tif .tiff .webp .heic .heif".split()
)
VIDEO_EXTS = frozenset(
    ".mp4 .mov .m4v .avi .mkv .webm .mpg .mpeg .wmv .3gp .m2ts .mts".split()
)


class ToolError(RuntimeError):
    """A media tool failed or was not understood. Never fatal to an ingest."""


def kind_for(path):
    ext = path.suffix.lower()
    if ext in PHOTO_EXTS:
        return "photo"
    if ext in VIDEO_EXTS:
        return "video"
    return "other"


def _run(cmd, binary=False):
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=TOOL_TIMEOUT_S, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolError(f"{cmd[0]} failed: {exc}") from exc
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ToolError(
            f"{cmd[0]} exited {proc.returncode}: {err[-1] if err else 'no output'}"
        )
    return proc.stdout if binary else proc.stdout.decode("utf-8", "replace")


_versions = {}


def tool_version(tool):
    """A short, stable version string for the provenance columns.

    Cached: a version string is worth recording but not worth a subprocess per
    file. Unreadable versions become "unknown" rather than raising, so a
    packaging quirk cannot stop an ingest.
    """
    if tool in _versions:
        return _versions[tool]
    patterns = {
        "identify": (["identify", "-version"], r"ImageMagick ([0-9][^\s]*)"),
        "ffprobe": (["ffprobe", "-version"], r"ffprobe version (\S+)"),
        "ffmpeg": (["ffmpeg", "-version"], r"ffmpeg version (\S+)"),
    }
    cmd, pattern = patterns[tool]
    try:
        match = re.search(pattern, _run(cmd))
        version = match.group(1) if match else "unknown"
    except ToolError:
        version = "unknown"
    _versions[tool] = version
    return version


# ---------------------------------------------------------------- photos


def photo_dimensions(path):
    """(width, height) via ImageMagick. Raises ToolError if unreadable."""
    out = _run(["identify", "-format", "%w %h", f"{path}[0]"])
    parts = out.strip().split()
    if len(parts) < 2:
        raise ToolError(f"identify gave no dimensions for {path}")
    try:
        return int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ToolError(f"identify gave non-numeric dimensions: {out!r}") from exc


def exif_properties(path):
    """Every EXIF property ImageMagick can see, as a plain dict.

    `%[EXIF:*]` prints `exif:Key=Value` lines. Files with no EXIF print
    nothing, which is a normal answer and not an error.
    """
    try:
        out = _run(["identify", "-format", "%[EXIF:*]", f"{path}[0]"])
    except ToolError:
        return {}
    props = {}
    for line in out.splitlines():
        if not line.startswith("exif:") or "=" not in line:
            continue
        key, _, value = line[len("exif:"):].partition("=")
        props[key.strip()] = value.strip()
    return props


_EXIF_DT = re.compile(r"^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})")


def parse_exif_datetime(value):
    """EXIF `YYYY:MM:DD HH:MM:SS` to ISO 8601, or None.

    No timezone is invented. EXIF DateTimeOriginal is local wall-clock with no
    offset recorded, and stamping a Z on it would be a fact the file never
    stated.
    """
    if not value:
        return None
    match = _EXIF_DT.match(value.strip())
    if not match:
        return None
    y, mo, d, h, mi, s = match.groups()
    try:
        datetime(int(y), int(mo), int(d), int(h), int(mi), int(s))
    except ValueError:
        return None
    return f"{y}-{mo}-{d}T{h}:{mi}:{s}"


def _rational(token):
    token = token.strip()
    if "/" in token:
        num, _, den = token.partition("/")
        den = float(den)
        if den == 0:
            return None
        return float(num) / den
    try:
        return float(token)
    except ValueError:
        return None


def _dms(value):
    """`41/1,52/1,4800/100` to decimal degrees."""
    parts = [_rational(p) for p in value.split(",")]
    if not parts or any(p is None for p in parts):
        return None
    parts = (parts + [0.0, 0.0])[:3]
    return parts[0] + parts[1] / 60.0 + parts[2] / 3600.0


def parse_gps(props):
    """(lat, lon) in signed decimal degrees from EXIF properties, or None."""
    lat = props.get("GPSLatitude")
    lon = props.get("GPSLongitude")
    if not lat or not lon:
        return None
    lat_d, lon_d = _dms(lat), _dms(lon)
    if lat_d is None or lon_d is None:
        return None
    if props.get("GPSLatitudeRef", "N").upper().startswith("S"):
        lat_d = -lat_d
    if props.get("GPSLongitudeRef", "E").upper().startswith("W"):
        lon_d = -lon_d
    if not (-90.0 <= lat_d <= 90.0 and -180.0 <= lon_d <= 180.0):
        return None
    # Six decimals is roughly 0.1 m. Rounding keeps the stored value stable
    # against float formatting drift between rebuilds.
    return round(lat_d, 6), round(lon_d, 6)


# ---------------------------------------------------------------- videos


def ffprobe_json(path):
    out = _run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ]
    )
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise ToolError(f"ffprobe emitted unparseable JSON for {path}") from exc


def video_summary(probe):
    """Pull the handful of keys we index out of an ffprobe document.

    Returns a dict of attribute -> (value, source_span). Missing keys are
    simply absent; a video with no duration reported gets no duration fact.
    """
    facts = {}
    streams = probe.get("streams") or []
    video = None
    for index, stream in enumerate(streams):
        if stream.get("codec_type") == "video":
            video, video_index = stream, index
            break
    if video is not None:
        for key in ("width", "height"):
            value = video.get(key)
            if isinstance(value, int):
                facts[key] = (float(value), f"ffprobe:streams[{video_index}].{key}")
        codec = video.get("codec_name")
        if isinstance(codec, str) and codec:
            facts["video_codec"] = (
                codec, f"ffprobe:streams[{video_index}].codec_name"
            )
    fmt = probe.get("format") or {}
    duration = fmt.get("duration")
    if duration is None and video is not None:
        duration = video.get("duration")
        span = "ffprobe:streams[].duration"
    else:
        span = "ffprobe:format.duration"
    try:
        if duration is not None:
            facts["duration_s"] = (round(float(duration), 3), span)
    except (TypeError, ValueError):
        pass
    return facts


def video_creation_time(probe):
    """(iso8601, source_span) from container tags, or None."""
    for scope, tags in (
        ("format", (probe.get("format") or {}).get("tags") or {}),
        *[
            (f"streams[{i}]", s.get("tags") or {})
            for i, s in enumerate(probe.get("streams") or [])
        ],
    ):
        raw = tags.get("creation_time")
        if not isinstance(raw, str):
            continue
        match = re.match(
            r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})", raw
        )
        if match:
            y, mo, d, h, mi, s = match.groups()
            return (
                f"{y}-{mo}-{d}T{h}:{mi}:{s}",
                f"ffprobe:{scope}.tags.creation_time",
            )
    return None


# ------------------------------------------------------- perceptual hash


GRAY_SAMPLE_CMD_SPAN = "ffmpeg:scale=8:8,format=gray"

# Below this spread across the 64 gray samples an image is effectively flat.
# Its average hash is decided by noise, and two unrelated flat images would
# match perfectly. Such items get a hash row at low confidence and are left
# out of grouping instead of being merged on nothing.
FLAT_RANGE = 8

# Out of 64 bits. Measured on the fixture set: a resized copy of the same
# image sits at distance 1, and the closest unrelated pair at 21. Eight is
# comfortably inside that gap and leaves room for re-encodes.
VARIANT_THRESHOLD = 8


def gray_samples(path):
    """The 64 bytes of an 8x8 grayscale reduction of the first frame."""
    out = _run(
        [
            "ffmpeg", "-v", "error", "-i", str(path),
            "-vf", "scale=8:8,format=gray", "-frames:v", "1",
            "-f", "rawvideo", "-",
        ],
        binary=True,
    )
    if len(out) < 64:
        raise ToolError(f"ffmpeg returned {len(out)} gray bytes for {path}")
    return out[:64]


def average_hash(samples):
    """(16-char hex hash, gray_range) from 64 gray samples."""
    average = sum(samples) / len(samples)
    bits = 0
    for i, value in enumerate(samples):
        if value > average:
            bits |= 1 << i
    return f"{bits:016x}", max(samples) - min(samples)


def hamming(hex_a, hex_b):
    return bin(int(hex_a, 16) ^ int(hex_b, 16)).count("1")


# ------------------------------------------------------------ thumbnails

THUMB_MAX_PX = 512


def thumb_size(width, height, max_px=THUMB_MAX_PX):
    """Fit inside max_px, preserving aspect, never upscaling."""
    if width <= 0 or height <= 0:
        return None
    if width <= max_px and height <= max_px:
        return width, height
    scale = max_px / float(max(width, height))
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))


def write_thumbnail(src, dst, size):
    """Render a JPEG thumbnail (photos) or first-frame poster (videos)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    width, height = size
    _run(
        [
            "ffmpeg", "-v", "error", "-y", "-i", str(src),
            "-frames:v", "1", "-vf", f"scale={width}:{height}",
            "-f", "image2", "-c:v", "mjpeg", str(dst),
        ]
    )
    if not dst.exists():
        raise ToolError(f"ffmpeg produced no thumbnail for {src}")
