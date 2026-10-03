import asyncio
import threading
import time
import types
import unittest
from unittest.mock import AsyncMock, patch
from PIL import Image
import test_minimal


class RecognitionSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if 'hoshino' not in __import__('sys').modules:
            test_minimal.MinimalBotTests.setUpClass()
            cls.addClassCleanup(test_minimal.MinimalBotTests.cleanup_environment)

    def test_public_recognition_runs_off_loop_and_rejects_parallel_work(self):
        from hoshino.modules.priconne.arena import old_main as recognition
        entered, release = threading.Event(), threading.Event()
        main_thread = threading.get_ident()
        threads = []

        async def computation(image):
            threads.append(threading.get_ident())
            entered.set()
            release.wait(3)
            return [], ''

        async def exercise():
            wrapped = recognition._in_worker(computation)
            task = asyncio.create_task(wrapped(Image.new('RGB', (10, 10))))
            try:
                for _ in range(100):
                    if entered.is_set():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(entered.is_set())
                with self.assertRaises(recognition.RecognitionBusy):
                    await wrapped(Image.new('RGB', (10, 10)))
            finally:
                release.set()
                await task
        asyncio.run(exercise())
        self.assertNotEqual(threads[0], main_thread)

    def test_oversized_image_rejected_before_recognition(self):
        from hoshino.modules.priconne.arena import old_main
        with patch.object(old_main, 'MAX_IMAGE_PIXELS', 10):
            with self.assertRaises(ValueError):
                asyncio.run(old_main.getBox(Image.new('RGB', (4, 4))))

    def test_download_limit_and_response_closed(self):
        from hoshino.modules.priconne.arena import old_main
        response = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock()
        response.__enter__.return_value = response
        response.headers = {'Content-Type': 'image/png'}
        response.iter_content.return_value = [b'123', b'456']
        with patch.object(old_main.requests, 'get', return_value=response), \
                patch.object(old_main, 'MAX_IMAGE_BYTES', 5):
            with self.assertRaises(ValueError):
                asyncio.run(old_main.get_pic('https://example.invalid/test.png'))
        response.__exit__.assert_called_once()

    def test_bad_image_has_user_reply(self):
        from hoshino.modules.priconne.arena import old_main
        bot = types.SimpleNamespace(send=AsyncMock())
        event = object()
        with patch.object(old_main, 'get_pic', AsyncMock(return_value=b'<html>error</html>')):
            asyncio.run(old_main._QueryArenaImageAsync('https://example.invalid/', 1, bot, event))
        bot.send.assert_awaited_once()
        self.assertIn('\u5931\u8d25', bot.send.await_args.args[1])
