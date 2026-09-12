"""The doctor must answer without a traceback on a machine where nothing is set up yet."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'doctor.py'

# scripts/ is not a package, so load the file by path the way a fresh clone would run it.
spec = importlib.util.spec_from_file_location('vault_doctor', SCRIPT)
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)


class Config(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix='doctor-'))
        self.addCleanup(lambda: __import__('shutil').rmtree(self.temp, ignore_errors=True))
        self.source = self.temp / 'library'
        self.source.mkdir()
        self.home = self.temp / 'repo'
        self.home.mkdir()

    def config(self, **overrides):
        return {'sources': [str(self.source)], **overrides}

    def test_missing_file_names_the_example(self):
        settings, errors = doctor.load_config(self.temp / 'absent.json', self.home)
        self.assertEqual(len(errors), 1)
        self.assertIn('vault.config.example.json', errors[0])
        self.assertIn('absent.json', errors[0])
        self.assertEqual(settings['state_path'], (self.home / '.catalog').resolve())

    def test_unparseable_file_is_a_row_not_an_exception(self):
        path = self.temp / 'broken.json'
        path.write_text('{not json')
        _, errors = doctor.load_config(path, self.home)
        self.assertTrue(any('could not be read' in e for e in errors))

    def test_valid_config_has_no_errors(self):
        settings, errors = doctor.validate_config(self.config(), self.home)
        self.assertEqual(errors, [])
        self.assertEqual(settings['sources_resolved'], [self.source.resolve()])
        self.assertEqual(settings['port'], 8770)
        self.assertEqual(settings['models_path'], (self.home / 'models').resolve())

    def test_unknown_key_is_an_error(self):
        _, errors = doctor.validate_config(self.config(catalog_dir='.catalog'), self.home)
        self.assertTrue(any('unknown keys: catalog_dir' in e for e in errors))

    def test_missing_source_is_an_error(self):
        _, errors = doctor.validate_config({'sources': [str(self.temp / 'gone')]}, self.home)
        self.assertTrue(any('does not exist' in e for e in errors))

    def test_empty_and_relative_sources_are_errors(self):
        _, errors = doctor.validate_config({'sources': []}, self.home)
        self.assertTrue(any('non-empty list' in e for e in errors))
        _, errors = doctor.validate_config({'sources': ['pictures']}, self.home)
        self.assertTrue(any('not an absolute path' in e for e in errors))

    def test_repeated_source_is_an_error(self):
        _, errors = doctor.validate_config({'sources': [str(self.source), str(self.source)]}, self.home)
        self.assertTrue(any('distinct' in e for e in errors))

    def test_state_dir_inside_a_source_is_an_error(self):
        _, errors = doctor.validate_config(self.config(state_dir=str(self.source / '.catalog')), self.home)
        self.assertTrue(any('derived state must stay outside originals' in e for e in errors))

    def test_models_dir_on_an_external_root_is_an_error(self):
        removable = self.temp / 'removable'
        (removable / 'models').mkdir(parents=True)
        _, errors = doctor.validate_config(
            self.config(models_dir=str(removable / 'models'), external_roots=[str(removable)]), self.home)
        self.assertTrue(any('removable media' in e for e in errors))

    def test_external_roots_can_be_emptied(self):
        removable = self.temp / 'removable'
        (removable / 'models').mkdir(parents=True)
        _, errors = doctor.validate_config(
            self.config(models_dir=str(removable / 'models'), external_roots=[]), self.home)
        self.assertEqual(errors, [])

    def test_bad_tier_port_and_exports(self):
        _, errors = doctor.validate_config(self.config(tier='public'), self.home)
        self.assertTrue(any('tier must be' in e for e in errors))
        _, errors = doctor.validate_config(self.config(port='8770'), self.home)
        self.assertTrue(any('port must be' in e for e in errors))
        # src/config.py, which the launcher uses, refuses privileged ports; the doctor must agree.
        _, errors = doctor.validate_config(self.config(port=80), self.home)
        self.assertTrue(any('port must be' in e for e in errors))
        _, errors = doctor.validate_config(self.config(exports='takeout'), self.home)
        self.assertTrue(any('exports must be an absolute folder path' in e for e in errors))
        _, errors = doctor.validate_config(self.config(exports=str(self.temp / 'gone')), self.home)
        self.assertTrue(any('exports folder does not exist' in e for e in errors))

    def test_exports_folder_that_exists_is_accepted(self):
        exports = self.temp / 'takeout'
        exports.mkdir()
        settings, errors = doctor.validate_config(self.config(exports=str(exports)), self.home)
        self.assertEqual(errors, [])
        self.assertEqual(settings['exports_path'], exports.resolve())

    def test_a_list_instead_of_an_object_is_an_error(self):
        _, errors = doctor.validate_config([str(self.source)], self.home)
        self.assertTrue(any('JSON object' in e for e in errors))


class Tessdata(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix='tessdata-'))
        self.addCleanup(lambda: __import__('shutil').rmtree(self.temp, ignore_errors=True))

    def test_prefix_pointing_at_the_data_folder(self):
        (self.temp / 'eng.traineddata').write_bytes(b'0')
        found = doctor.tessdata_english({'TESSDATA_PREFIX': str(self.temp), 'PATH': ''})
        self.assertEqual(found, self.temp / 'eng.traineddata')

    def test_prefix_pointing_at_the_parent_of_tessdata(self):
        (self.temp / 'tessdata').mkdir()
        (self.temp / 'tessdata' / 'eng.traineddata').write_bytes(b'0')
        found = doctor.tessdata_english({'TESSDATA_PREFIX': str(self.temp), 'PATH': ''})
        self.assertEqual(found, self.temp / 'tessdata' / 'eng.traineddata')

    def test_extra_directory_is_searched_after_the_prefix(self):
        (self.temp / 'eng.traineddata').write_bytes(b'0')
        found = doctor.tessdata_english({'PATH': ''}, extra_dirs=[self.temp])
        self.assertEqual(found, self.temp / 'eng.traineddata')

    def test_empty_candidates_never_answer(self):
        # The system folders are always searched last, so this machine may still have the data.
        # What must not happen is a hit inside a folder that holds nothing.
        empty = self.temp / 'empty'
        empty.mkdir()
        found = doctor.tessdata_english({'TESSDATA_PREFIX': str(empty), 'PATH': ''}, extra_dirs=[empty])
        self.assertTrue(found is None or empty not in found.parents, found)


class Helpers(unittest.TestCase):
    def test_run_survives_a_missing_binary(self):
        self.assertEqual(doctor.run(['definitely-not-a-real-binary-xyz', '--version']), '')

    def test_count_survives_a_missing_database(self):
        self.assertIsNone(doctor.count(Path(tempfile.gettempdir()) / 'no-such-catalog.db', 'SELECT count(*) FROM files'))

    def test_server_check_on_a_closed_port(self):
        # Port 1 needs privileges to bind, so nothing local answers there.
        self.assertEqual(doctor.check_server(1), {'running': False, 'files': None})

    def test_suggestion_follows_the_gpu(self):
        items = {i['model']: i for i in doctor.suggest({'gpu': None})['items']}
        self.assertTrue(items['faces']['recommended'])
        self.assertFalse(items['siglip2']['recommended'])
        self.assertFalse(items['descriptions and quality']['recommended'])
        big = {i['model']: i for i in doctor.suggest({'gpu': {'name': 'Test GPU', 'memory_total_mb': 16384}})['items']}
        self.assertTrue(big['siglip2']['recommended'])
        self.assertTrue(big['descriptions and quality']['recommended'])
        small = {i['model']: i for i in doctor.suggest({'gpu': {'name': 'Test GPU', 'memory_total_mb': 4096}})['items']}
        self.assertTrue(small['siglip2']['recommended'])
        self.assertFalse(small['descriptions and quality']['recommended'])


class Report(unittest.TestCase):
    def test_json_output_parses_on_this_machine(self):
        done = subprocess.run([sys.executable, str(SCRIPT), '--json'], capture_output=True, text=True, timeout=120)
        self.assertIn(done.returncode, (0, 1), done.stderr)
        report = json.loads(done.stdout)
        for key in ('rows', 'ready', 'suggestion', 'hardware', 'platform', 'config_errors', 'next'):
            self.assertIn(key, report)
        self.assertIsInstance(report['ready'], bool)
        self.assertTrue(report['rows'])
        for row in report['rows']:
            self.assertEqual(set(row), {'name', 'status', 'detail'})
            self.assertIn(row['status'], {'OK', 'MISSING', 'WARN', 'OPTIONAL'})
        names = {row['name'] for row in report['rows']}
        self.assertLessEqual({'python', 'venv', 'ffmpeg', 'ffprobe', 'imagemagick', 'tesseract', 'config', 'state',
                              'server', 'package numpy', 'model faces'}, names)
        self.assertEqual(report['ready'], not [r for r in report['rows']
                                               if r['name'] in doctor.GATES and r['status'] == 'MISSING'])
        self.assertIn('cpu_count', report['hardware'])
        self.assertTrue(report['suggestion']['items'])

    def test_human_output_ends_with_a_verdict_and_a_next_line(self):
        done = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=120)
        lines = done.stdout.strip().splitlines()
        self.assertTrue(lines[-2].startswith('READY') or lines[-2].startswith('NOT READY'), done.stdout)
        self.assertTrue(lines[-1].startswith('Next: '), done.stdout)
        self.assertEqual(done.returncode, 0 if lines[-2].startswith('READY') else 1)


if __name__ == '__main__':
    unittest.main()
