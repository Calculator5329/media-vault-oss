"""Persistent units preserve venv execution and safe source arguments."""
import os
os.environ.setdefault('MEDIA_VAULT_EXTERNAL_ROOTS', '/run/media')  # the tests use /run/media as the example removable root
import json
from pathlib import Path
import tempfile
import unittest
from src.services import render,quoted

class ServiceTests(unittest.TestCase):
    def test_runtime_paths_and_source_spaces_survive_rendering(self):
        root=Path(tempfile.mkdtemp());repo=root/'synthetic repo';repo.mkdir()
        (repo/'vault.config.json').write_text(json.dumps({'sources':['/run/media/synthetic photos']}))
        resources=root/'resources.json';python=root/'venv/bin/python'
        resources.write_text(json.dumps({'vision_python':str(python),'vision_model':str(root/'model')}))
        units=render(repo,resources,'/run/media/synthetic exports',root/'logs',root/'kit')
        self.assertEqual(len(units),3)
        self.assertIn('WorkingDirectory='+str(root/'logs')+'\n',units['media-vault-viewer.service'])
        self.assertNotIn('After=default.target',units['media-vault-viewer.service'])
        self.assertIn(quoted(python),units['media-vault-enrichment.service'])
        self.assertIn('"/run/media/synthetic exports"',units['media-vault-imports.service'])
        self.assertIn('WantedBy=default.target',units['media-vault-viewer.service'])
        self.assertNotIn('sudo', ''.join(units.values()))
        self.assertNotIn('Restart=always', ''.join(units.values()))
        with self.assertRaises(ValueError):render(repo,resources,'/run/media/synthetic exports','/run/media/logs',root/'kit')

    def test_unit_expansions_cannot_reinterpret_literal_arguments(self):
        self.assertEqual(quoted('a%u$b',exec_argument=True), '"a%%u$$b"')
        with self.assertRaises(ValueError):quoted('bad\nExecStart=anything')
