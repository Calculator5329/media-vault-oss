import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from src.survey import survey,sidecar_presence

class SurveyTests(unittest.TestCase):
    def test_sidecar_missing_gps_and_zero_placeholder_are_not_present(self):
        self.assertNotIn("sidecar_gps",sidecar_presence({"geoData":{"latitude":0,"longitude":0}}))
        self.assertEqual(sidecar_presence({"photoTakenTime":{"timestamp":"123"},"geoDataExif":{"latitude":1,"longitude":2}}),{"sidecar_json":1,"sidecar_taken_at":1,"sidecar_gps":1})
    def test_denominators_sample_and_sources_are_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for i in range(9):(root/f"fictional{i}.jpg").write_bytes(b"synthetic")
            (root/"fictional0.jpg.json").write_text(json.dumps({"photoTakenTime":{"timestamp":"123"}}))
            before={p.name:p.read_bytes() for p in root.iterdir()}
            with patch("src.survey.probe.exif_properties",return_value={"DateTimeOriginal":"2025:01:01 00:00:00"}),patch("src.survey.probe.tool_version",return_value="fixture"):
                result=survey([root],per_shape=3,workers=2)
            self.assertEqual(result["census"][0]["files"],10)
            self.assertEqual(sum(r["population"] for r in result["coverage"]),9)
            self.assertEqual(sum(r["probed"] for r in result["coverage"]),4)
            self.assertEqual(before,{p.name:p.read_bytes() for p in root.iterdir()})
            self.assertNotIn("fictional0",json.dumps(result))
