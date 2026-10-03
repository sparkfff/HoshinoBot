"""Offline regressions for roster updates and plugin resource interfaces."""
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import test_minimal


class DataSafetyTests(unittest.TestCase):
    setUpClass = classmethod(test_minimal.MinimalBotTests.setUpClass.__func__)
    cleanup_environment = classmethod(test_minimal.MinimalBotTests.cleanup_environment.__func__)

    def setUp(self):
        from hoshino.modules.priconne import chara
        self.chara = chara
        self.old_values = {k: v for k, v in vars(chara._pcr_data).items() if not k.startswith('_')}
        self.old_trie = chara.roster._roster
        self.addCleanup(chara.roster.install, self.old_values, self.old_trie)

    def source(self):
        return "CHARA_NAME = {1000: ['Unknown'], 1001: ['New alias']}\nUnavailableChara = {1002}\nCHARA_PROFILE = {1001: {'名字': 'New'}}\nEXTRA_DATA = ('retained', 1)\n"

    def test_remote_code_rejected_without_execution(self):
        from hoshino.modules.priconne.pcr_data_updater import install_roster_source
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / '_pcr_data.py'
            target.write_text('old data', encoding='utf8')
            for source in [self.source() + "import os\n", self.source() + "X = open('executed', 'w')\n",
                           "CHARA_NAME = {1000: []}\nUnavailableChara = {}\nCHARA_PROFILE = {}\n"]:
                with self.assertRaises(ValueError):
                    install_roster_source(source, str(target))
                self.assertEqual(target.read_text(encoding='utf8'), 'old data')
                self.assertIs(self.chara.roster._roster, self.old_trie)

    def test_atomic_write_failure_preserves_roster_and_file(self):
        from hoshino.modules.priconne.pcr_data_updater import install_roster_source
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / '_pcr_data.py'
            target.write_text('old data', encoding='utf8')
            with patch('hoshino.modules.priconne.pcr_data_updater.os.replace', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    install_roster_source(self.source(), str(target))
            self.assertEqual(target.read_text(encoding='utf8'), 'old data')
            self.assertIs(self.chara.roster._roster, self.old_trie)
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_update_publishes_all_constants_and_reloads_without_exec(self):
        from hoshino.modules.priconne.pcr_data_updater import install_roster_source
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / '_pcr_data.py'
            result = install_roster_source(self.source(), str(target))
            self.assertEqual(result['success'], 2)
            self.assertEqual(self.chara.name2id('New alias'), 1001)
            self.assertEqual(self.chara._pcr_data.CHARA_PROFILE, {1001: {'名字': 'New'}})
            self.assertEqual(self.chara._pcr_data.EXTRA_DATA, ('retained', 1))
            with patch.object(self.chara._pcr_data, '__file__', str(target)):
                self.chara.roster.update()
            self.assertEqual(self.chara.name2id('New alias'), 1001)
        del self.chara._pcr_data.EXTRA_DATA

    def test_deprecated_icon_returns_fallback_inside_running_loop(self):
        from hoshino import R
        async def exercise():
            with self.assertWarns(DeprecationWarning):
                icon = self.chara.Chara(999999).icon
            self.assertEqual(icon.path, R.img('priconne/unit/icon_unit_100031.png').path)
        with patch.object(self.chara, 'download_chara_icon', AsyncMock()) as download:
            asyncio.run(exercise())
        download.assert_not_called()

    def test_resource_rejects_sibling_prefix_and_absolute_escape(self):
        from hoshino import R
        root = Path(self.hoshino.config.RES_DIR)
        for path in ['../res-other/secret', str(root.parent / 'res-other' / 'secret')]:
            with self.assertRaises(ValueError):
                R.get(path)
        self.assertEqual(R.get('nested/file').path, str(root / 'nested/file'))

    def test_resource_rejects_symlink_escape_and_rechecks_existing_object(self):
        from hoshino import R
        root = Path(self.hoshino.config.RES_DIR)
        root.mkdir(parents=True, exist_ok=True)
        link = root / 'link'
        resource = R.get('link/secret')
        with tempfile.TemporaryDirectory() as directory:
            try:
                link.symlink_to(directory, target_is_directory=True)
            except OSError as error:
                self.skipTest(f'symlink unavailable: {error}')
            try:
                with self.assertRaises(ValueError):
                    R.get('link/secret')
                with self.assertRaises(ValueError):
                    _ = resource.path
                with self.assertRaises(ValueError):
                    _ = resource.url
            finally:
                link.unlink()


if __name__ == '__main__':
    unittest.main()
