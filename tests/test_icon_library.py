"""Offline bulk icon download regressions."""
import asyncio
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch
from PIL import Image
import test_minimal


class IconLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if 'hoshino' not in sys.modules:
            test_minimal.MinimalBotTests.setUpClass.__func__(cls)
        else:
            import hoshino
            cls.hoshino = hoshino

    cleanup_environment = classmethod(test_minimal.MinimalBotTests.cleanup_environment.__func__)

    def test_download_missing_variants_with_bounded_workers(self):
        from hoshino.modules.priconne import chara
        active, maximum = 0, 0
        calls = []
        async def download(id_, star):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0)
            active -= 1
            calls.append((id_, star))
            return 2 if star == 6 else (1 if id_ == 1002 else 0)
        with patch.object(chara._pcr_data, 'CHARA_NAME', {1000: ['unknown'], 1001: ['a'],
                1002: ['b'], 1901: ['npc']}), \
                patch.object(chara, '_icon_is_valid', side_effect=lambda i, s: (i, s) == (1001, 1)), \
                patch.object(chara, 'download_chara_icon', side_effect=download):
            result = asyncio.run(chara.download_icon_library())
        self.assertEqual(result, {'downloaded': 1, 'existing': 1, 'unavailable': 2, 'failed': 2})
        self.assertLessEqual(maximum, 2)
        self.assertEqual(set(calls), {(1001, 3), (1001, 6), (1002, 1), (1002, 3), (1002, 6)})

    def test_command_rebuilds_library_and_rejects_duplicate(self):
        from hoshino.modules.priconne import chara
        from hoshino.modules.priconne.arena import old_main
        sess = types.SimpleNamespace(send=AsyncMock())
        counts = {'downloaded': 1, 'existing': 2, 'unavailable': 3, 'failed': 0}
        with patch.object(chara, 'download_icon_library', AsyncMock(return_value=counts)) as download, \
                patch.object(old_main, '_update_dic', types.SimpleNamespace(__wrapped__=AsyncMock())) as rebuild:
            asyncio.run(chara.download_full_icon_library.__wrapped__(sess))
            rebuild.__wrapped__.assert_awaited_once_with(sess)
            self.assertEqual(download.await_args.args[0], (1, 3, 6))
            chara._library_download_lock.acquire()
            try:
                asyncio.run(chara.download_full_icon_library.__wrapped__(sess))
            finally:
                chara._library_download_lock.release()
            download.assert_awaited_once()

    def test_download_404_closes_response_without_file(self):
        from hoshino.modules.priconne import chara
        response = types.SimpleNamespace(status_code=404, raw_response=Mock())
        with patch.object(chara.aiorequests, 'get', AsyncMock(return_value=response)):
            self.assertEqual(asyncio.run(chara.download_chara_icon(9999, 6)), 2)
        response.raw_response.close.assert_called_once()

    def test_corrupt_png_is_not_skipped(self):
        from hoshino.modules.priconne import chara
        from hoshino import R
        import tempfile
        with tempfile.TemporaryDirectory() as directory, patch.object(self.hoshino.config, 'RES_DIR', directory):
            path = __import__('pathlib').Path(R.img('priconne/unit/icon_unit_100131.png').path)
            path.parent.mkdir(parents=True)
            path.write_bytes(b'incomplete')
            self.assertFalse(chara._icon_is_valid(1001, 3))
            Image.new('RGB', (128, 128)).save(path)
            self.assertTrue(chara._icon_is_valid(1001, 3))
