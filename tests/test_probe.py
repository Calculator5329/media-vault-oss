"""The parsing seam: what we make of what the tools print.

These run against captured tool output rather than files, so a change in how
EXIF dates or ffprobe documents are read fails here, precisely, instead of
somewhere downstream in an ingest.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import probe


class KindTests(unittest.TestCase):
    def test_extension_decides_kind_case_insensitively(self):
        self.assertEqual(probe.kind_for(Path("a/b.JPG")), "photo")
        self.assertEqual(probe.kind_for(Path("a/b.mp4")), "video")
        self.assertEqual(probe.kind_for(Path("a/b.MOV")), "video")
        self.assertEqual(probe.kind_for(Path("a/b.txt")), "other")
        self.assertEqual(probe.kind_for(Path("a/b")), "other")


class ExifParsingTests(unittest.TestCase):
    def test_exif_datetime_to_iso(self):
        self.assertEqual(
            probe.parse_exif_datetime("2026:07:04 10:20:30"),
            "2026-07-04T10:20:30",
        )

    def test_no_timezone_is_invented(self):
        parsed = probe.parse_exif_datetime("2026:07:04 10:20:30")
        self.assertFalse(parsed.endswith("Z"))
        self.assertNotIn("+", parsed)

    def test_junk_and_impossible_dates_are_none(self):
        for value in ("", None, "not a date", "0000:00:00 00:00:00",
                      "2026:13:04 10:20:30", "2026:07:04 99:20:30"):
            self.assertIsNone(probe.parse_exif_datetime(value), value)

    def test_calendar_and_clock_fields_are_validated(self):
        for value in ("2026:02:31 12:99:99", "2026:02:29 12:30:59",
                      "1900:02:29 12:30:59", "2026:04:31 12:30:59",
                      "2026:07:04 12:60:30", "2026:07:04 12:30:60",
                      "0000:01:01 00:00:00"):
            with self.subTest(value=value):
                self.assertIsNone(probe.parse_exif_datetime(value))
        for value, expected in (("2024:02:29 12:30:59", "2024-02-29T12:30:59"),
                                ("2000:02:29T00:00:00", "2000-02-29T00:00:00"),
                                (" 2026:07:04 23:59:59 ", "2026-07-04T23:59:59")):
            with self.subTest(value=value):
                self.assertEqual(probe.parse_exif_datetime(value), expected)

    def test_gps_rationals_to_signed_decimal(self):
        props = {
            "GPSLatitude": "4100/100,5200/100,4800/100",
            "GPSLatitudeRef": "N",
            "GPSLongitude": "8700/100,3700/100,1200/100",
            "GPSLongitudeRef": "W",
        }
        lat, lon = probe.parse_gps(props)
        self.assertAlmostEqual(lat, 41.88, places=5)
        self.assertAlmostEqual(lon, -87.62, places=5)

    def test_southern_and_eastern_hemispheres(self):
        props = {
            "GPSLatitude": "33/1,52/1,0/1",
            "GPSLatitudeRef": "S",
            "GPSLongitude": "151/1,12/1,0/1",
            "GPSLongitudeRef": "E",
        }
        lat, lon = probe.parse_gps(props)
        self.assertLess(lat, 0)
        self.assertGreater(lon, 0)

    def test_partial_or_broken_gps_is_none(self):
        self.assertIsNone(probe.parse_gps({}))
        self.assertIsNone(probe.parse_gps({"GPSLatitude": "41/1,52/1,48/1"}))
        self.assertIsNone(probe.parse_gps({
            "GPSLatitude": "41/0,52/1,48/1", "GPSLatitudeRef": "N",
            "GPSLongitude": "87/1,37/1,12/1", "GPSLongitudeRef": "W",
        }))

    def test_out_of_range_coordinates_rejected(self):
        self.assertIsNone(probe.parse_gps({
            "GPSLatitude": "910/1,0/1,0/1", "GPSLatitudeRef": "N",
            "GPSLongitude": "87/1,37/1,12/1", "GPSLongitudeRef": "W",
        }))


class FfprobeParsingTests(unittest.TestCase):
    DOCUMENT = {
        "streams": [
            {"codec_type": "audio", "codec_name": "aac"},
            {
                "codec_type": "video", "codec_name": "h264",
                "width": 320, "height": 240,
                "tags": {"creation_time": "2026-03-02T04:05:06.000000Z"},
            },
        ],
        "format": {"duration": "1.000000", "tags": {}},
    }

    def test_video_stream_found_past_an_audio_stream(self):
        facts = probe.video_summary(self.DOCUMENT)
        self.assertEqual(facts["width"][0], 320.0)
        self.assertEqual(facts["height"][0], 240.0)
        self.assertEqual(facts["video_codec"][0], "h264")

    def test_source_span_names_the_ffprobe_key(self):
        facts = probe.video_summary(self.DOCUMENT)
        self.assertEqual(facts["width"][1], "ffprobe:streams[1].width")
        self.assertEqual(facts["duration_s"][1], "ffprobe:format.duration")

    def test_duration_parsed_as_seconds(self):
        facts = probe.video_summary(self.DOCUMENT)
        self.assertAlmostEqual(facts["duration_s"][0], 1.0, places=3)

    def test_creation_time_found_on_a_stream_and_span_recorded(self):
        stamp, span = probe.video_creation_time(self.DOCUMENT)
        self.assertEqual(stamp, "2026-03-02T04:05:06")
        self.assertEqual(span, "ffprobe:streams[1].tags.creation_time")

    def test_empty_document_yields_no_facts(self):
        self.assertEqual(probe.video_summary({}), {})
        self.assertIsNone(probe.video_creation_time({}))

    def test_unparseable_duration_is_dropped_not_guessed(self):
        facts = probe.video_summary({"format": {"duration": "N/A"}})
        self.assertNotIn("duration_s", facts)


class HashAndThumbTests(unittest.TestCase):
    def test_average_hash_splits_on_the_mean(self):
        samples = bytes([0] * 32 + [255] * 32)
        digest, gray_range = probe.average_hash(samples)
        self.assertEqual(gray_range, 255)
        self.assertEqual(bin(int(digest, 16)).count("1"), 32)

    def test_flat_image_reports_zero_range(self):
        _, gray_range = probe.average_hash(bytes([128] * 64))
        self.assertEqual(gray_range, 0)
        self.assertLess(gray_range, probe.FLAT_RANGE)

    def test_hamming_distance(self):
        self.assertEqual(probe.hamming("0000000000000000", "0000000000000001"), 1)
        self.assertEqual(probe.hamming("ffffffffffffffff", "ffffffffffffffff"), 0)

    def test_thumbnail_fits_the_box_without_upscaling(self):
        self.assertEqual(probe.thumb_size(1024, 768), (512, 384))
        self.assertEqual(probe.thumb_size(768, 1024), (384, 512))
        self.assertEqual(probe.thumb_size(320, 240), (320, 240))
        self.assertIsNone(probe.thumb_size(0, 100))


if __name__ == "__main__":
    unittest.main()
