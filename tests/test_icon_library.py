"""Offline bulk icon download regressions."""
import asyncio
import sys
import types
import tempfile
import time
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

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        resource_patch = patch.object(self.hoshino.config, 'RES_DIR', directory.name)
        resource_patch.start()
        self.addCleanup(resource_patch.stop)

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
        self.assertEqual(result, {'downloaded': 1, 'existing': 1, 'unavailable': 2,
                                 'cached_unavailable': 0, 'failed': 2})
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

    def test_404_survives_restart_and_avoids_repeated_requests(self):
        from hoshino.modules.priconne import chara
        with patch.object(chara._pcr_data, 'CHARA_NAME', {1001: ['a']}), \
                patch.object(chara, '_icon_is_valid', return_value=False), \
                patch.object(chara, 'download_chara_icon', AsyncMock(return_value=2)) as download:
            first = asyncio.run(chara.download_icon_library((6,)))
            self.assertEqual(first['unavailable'], 1)
            download.reset_mock()
            second = asyncio.run(chara.download_icon_library((6,)))
            self.assertEqual(second['cached_unavailable'], 1)
            download.assert_not_awaited()
            asyncio.run(chara.download_icon_library((6,), retry_missing=True))
            download.assert_awaited_once()

    def test_failed_download_retried_and_expired_404_rechecked(self):
        from hoshino.modules.priconne import chara
        with patch.object(chara._pcr_data, 'CHARA_NAME', {1001: ['a']}), \
                patch.object(chara, '_icon_is_valid', return_value=False), \
                patch.object(chara, 'download_chara_icon', AsyncMock(return_value=1)) as download:
            asyncio.run(chara.download_icon_library((6,)))
            asyncio.run(chara.download_icon_library((6,)))
            self.assertEqual(download.await_count, 2)
            chara._save_missing_icons({'100161': time.time() - chara._MISSING_ICON_TTL - 1})
            asyncio.run(chara.download_icon_library((6,)))
            self.assertEqual(download.await_count, 3)

    def test_existing_icons_and_cached_missing_have_no_download_jobs(self):
        from hoshino.modules.priconne import chara
        chara._save_missing_icons({'100161': time.time()})
        progress = AsyncMock()
        with patch.object(chara._pcr_data, 'CHARA_NAME', {1001: ['a']}), \
                patch.object(chara, '_icon_is_valid', side_effect=lambda i, s: s in (1, 3)), \
                patch.object(chara, 'download_chara_icon', AsyncMock()) as download:
            result = asyncio.run(chara.download_icon_library(progress=progress))
        self.assertEqual(result['existing'], 2)
        self.assertEqual(result['cached_unavailable'], 1)
        progress.assert_awaited_once_with(0, 0)
        download.assert_not_awaited()
