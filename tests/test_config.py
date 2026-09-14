"""vault.config.json: the tool_paths key, and the config the setup command writes."""
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

from src import config
from tests.scratch import scratch

REPO = Path(__file__).resolve().parents[1]


def load_setup_module():
    """scripts/setup.py is a script, not a package member; import it by path."""
    spec = importlib.util.spec_from_file_location('vault_setup', REPO / 'scripts/setup.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ToolPathTests(unittest.TestCase):
    def setUp(self):
        self.root = scratch()
        self.source = self.root / 'photos'
        self.source.mkdir()
        self.tools = self.root / 'bin'
        self.tools.mkdir()

    def write(self, **extra):
        path = self.root / 'vault.config.json'
        path.write_text(json.dumps({'sources': [str(self.source)], 'state_dir': str(self.root / 'state'),
                                    'models_dir': str(self.root / 'models'), **extra}))
        return path

    def test_tool_paths_reach_the_path_of_every_command(self):
        """A Windows installer that never touched PATH is why this key exists."""
        value = config.load(self.write(tool_paths=[str(self.tools)]))
        self.assertEqual(value['tool_paths'], [str(self.tools)])
        env = config.apply_environment(value, {'PATH': '/usr/bin'})
        self.assertEqual(env['PATH'].split(os.pathsep)[0], str(self.tools))
        # Already on PATH: recorded once, not prepended again.
        again = config.apply_environment(value, {'PATH': os.pathsep.join([str(self.tools), '/usr/bin'])})
        self.assertEqual(again['PATH'].split(os.pathsep).count(str(self.tools)), 1)

    def test_no_tool_paths_leaves_path_alone(self):
        value = config.load(self.write())
        self.assertEqual(value['tool_paths'], [])
        self.assertEqual(config.apply_environment(value, {'PATH': '/usr/bin'})['PATH'], '/usr/bin')

    def test_a_tool_paths_value_that_is_not_a_list_of_folders_is_an_error(self):
        for bad in ('/usr/bin', [1], ['']):
            with self.assertRaises(config.ConfigError):
                config.load(self.write(tool_paths=bad))


class SetupConfigTests(unittest.TestCase):
    def test_the_written_config_has_no_carriage_returns(self):
        """Python text mode would write CRLF on Windows and turn every later edit into a whole-file diff."""
        setup = load_setup_module()
        original = setup.CONFIG.read_bytes() if setup.CONFIG.is_file() else None
        self.addCleanup(lambda: setup.CONFIG.write_bytes(original) if original is not None
                        else setup.CONFIG.unlink(missing_ok=True))
        setup.write_config({'sources': ['/photos'], 'port': 8770})
        self.assertNotIn(b'\r', setup.CONFIG.read_bytes())
        self.assertEqual(json.loads(setup.CONFIG.read_text())['port'], 8770)

    def test_every_key_setup_writes_is_a_key_the_config_accepts(self):
        setup = load_setup_module()
        self.assertEqual(set(setup.CONFIG_DEFAULTS) | {'sources'}, set(config.DEFAULTS))

    def test_the_offer_recommends_vision_only_when_a_gpu_is_present(self):
        """CLAUDE.md has the agent read this offer back before running setup, so the GPU decides."""
        setup = load_setup_module()
        gpu = {'gpu': {'name': 'RTX 4070', 'memory_total_mb': 12288, 'memory_free_mb': 12000},
               'ram_gb': 32.0, 'cpu_count': 16, 'disk_free_gb': 200.0, 'disk_path': '.catalog'}
        with_gpu = setup.offer(gpu, ['D:\\Pictures'])
        without = setup.offer({**gpu, 'gpu': None}, ['D:\\Pictures'])
        vision = {item['flag']: item for item in with_gpu['extras']}['--vision']
        self.assertTrue(vision['recommended'])
        self.assertIn('RTX 4070', vision['why'])
        self.assertEqual(with_gpu['suggested_command'], 'python vault.py setup "D:\\Pictures" --vision')
        self.assertFalse({item['flag']: item for item in without['extras']}['--vision']['recommended'])
        self.assertEqual(without['suggested_command'], 'python vault.py setup "D:\\Pictures"')
        self.assertEqual(with_gpu['default_download_mb'], 37 + 13 + 461)
        self.assertEqual(setup.offer({**gpu, 'disk_free_gb': 3.0}, ['x'])['warnings'],
                         ['only 3.0 GB free where the catalog will go; previews and models need room'])


if __name__ == '__main__':
    unittest.main()
