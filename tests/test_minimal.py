"""Offline checks for the slim bot; no QQ connection or real HTTP requests."""
import asyncio
import io
import importlib.abc
import importlib.util
import logging
import os
from pathlib import Path
import runpy
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class MinimalBotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import nonebot.default_config

        cls.temp = tempfile.TemporaryDirectory()
        cls.old_cwd = Path.cwd()
        cls.addClassCleanup(cls.cleanup_environment)
        os.chdir(cls.temp.name)
        real_expanduser = os.path.expanduser
        cls.home_patch = patch('os.path.expanduser', side_effect=lambda path:
            str(Path(cls.temp.name) / path[2:]) if path.startswith('~/')
            else real_expanduser(path))
        cls.home_patch.start()
        config = types.ModuleType('hoshino.config')
        config.__dict__.update({k: v for k, v in vars(nonebot.default_config).items()
                                if not k.startswith('__')})
        config.__dict__.update({k: v for k, v in runpy.run_path(
            str(ROOT / 'hoshino/config_example/__bot__.py')).items()
            if not k.startswith('__')})
        config.RES_DIR = str(Path(cls.temp.name) / 'res')
        config.priconne = types.SimpleNamespace(**{
            k: v for k, v in runpy.run_path(
                str(ROOT / 'hoshino/config_example/priconne.py')).items()
            if not k.startswith('__')})
        class ConfigLoader(importlib.abc.Loader):
            def create_module(self, spec):
                return None

            def exec_module(self, module):
                module.__dict__.update(config.__dict__)

        class ConfigFinder(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):
                if fullname == 'hoshino.config':
                    return importlib.util.spec_from_loader(fullname, ConfigLoader())

        finder = ConfigFinder()
        sys.meta_path.insert(0, finder)
        try:
            import hoshino
        finally:
            sys.meta_path.remove(finder)
        cls.bot = hoshino.init()
        cls.hoshino = hoshino

    @classmethod
    def cleanup_environment(cls):
        logging.shutdown()
        cls.home_patch.stop()
        os.chdir(cls.old_cwd)
        cls.temp.cleanup()

    def test_only_expected_builtin_services_load(self):
        self.assertEqual(self.hoshino.config.MODULES_ON, {'botmanage', 'priconne'})
        self.assertEqual(set(self.hoshino.Service.get_loaded_services()),
                         {'pcr-query', 'pcr-arena', 'pcr-data-updater', '_help_', '_feedback_'})
        self.assertFalse(self.hoshino.modules.priconne.chara._render_assets)
        self.assertNotIn('matplotlib', sys.modules)
        import nonebot
        self.assertEqual(nonebot.scheduler.get_jobs(), [])

    def _query(self, text, uid, icon_exists=False):
        from hoshino.msghandler import handle_message
        from hoshino.modules.priconne import chara
        from hoshino.typing import CQEvent, Message
        event = CQEvent({'post_type': 'message', 'message_type': 'group',
                         'sub_type': 'normal', 'group_id': 123, 'user_id': uid,
                         'self_id': 10000, 'message_id': uid, 'to_me': False,
                         'anonymous': None, 'sender': {'role': 'member'},
                         'message': Message(text)})
        bot = types.SimpleNamespace(send=AsyncMock())
        icon = types.SimpleNamespace(exist=icon_exists,
                                     cqcode='[CQ:image,file=test.png]')
        async def dispatch():
            try:
                await handle_message(bot, event, None)
            except self.hoshino.CanceledException:
                pass
        with patch.object(chara.Chara, 'get_icon', AsyncMock(return_value=icon)):
            asyncio.run(dispatch())
        return bot.send.await_args_list

    def test_whois_prefix_without_images(self):
        from hoshino.modules.priconne import chara
        calls = self._query('谁是霸瞳', 201)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[1], chara.fromname('霸瞳').name)

    def test_whois_suffix_with_image(self):
        calls = self._query('霸瞳是谁', 202, icon_exists=True)
        self.assertEqual(len(calls), 1)
        self.assertIn('[CQ:image', calls[0].args[1])

    def test_whois_fuzzy_lookup(self):
        from hoshino.modules.priconne import chara
        with patch.object(chara, 'guess_id', return_value=(
                chara.name2id('霸瞳'), '霸瞳', 80)):
            calls = self._query('谁是测试拼写错误', 203)
        self.assertEqual(len(calls), 2)
        self.assertIn('80%', calls[1].args[1])

    def test_removed_commands_do_not_trigger(self):
        for text in ('十连', '!查刀', '猜头像', '.r 3d12'):
            self.assertEqual(self._query(text, 204), [])

    def test_icon_download_creates_resource_directory(self):
        import requests
        from PIL import Image
        from hoshino import aiorequests, R
        from hoshino.modules.priconne import chara
        buffer = io.BytesIO()
        Image.new('RGB', (8, 8)).save(buffer, format='PNG')
        response = requests.Response()
        response.status_code = 200
        response._content = buffer.getvalue()
        with patch.object(aiorequests, 'get', AsyncMock(
                return_value=aiorequests.AsyncResponse(response))):
            result = asyncio.run(chara.download_chara_icon(9999, 3))
        self.assertEqual(result, 0)
        self.assertTrue(R.img('priconne/unit/icon_unit_999931.png').exist)

    def test_third_party_plugin_loader_remains_available(self):
        import nonebot
        package = Path(self.temp.name) / 'external_plugins'
        package.mkdir()
        (package / '__init__.py').write_text('', encoding='utf-8')
        (package / 'sample.py').write_text(
            "from hoshino import Service\n"
            "sv = Service('external-test')\n"
            "@sv.on_fullmatch('插件测试')\n"
            "async def reply(bot, ev):\n"
            "    await bot.send(ev, '插件已加载')\n", encoding='utf-8')
        sys.path.insert(0, self.temp.name)
        try:
            loaded = nonebot.load_plugins(str(package), 'external_plugins')
            self.assertTrue(loaded)
            calls = self._query('插件测试', 205)
            self.assertEqual(calls[0].args[1], '插件已加载')
        finally:
            self.hoshino.Service.get_loaded_services().pop('external-test', None)
            sys.path.remove(self.temp.name)

    def test_arena_query_interface(self):
        from hoshino.modules.priconne.arena import arena
        from hoshino import aiorequests
        response = types.SimpleNamespace(json=AsyncMock(return_value={
            'code': 0, 'data': {'result': [{
                'id': '123456', 'atk': [{'id': 100101, 'star': 3, 'equip': 0}],
                'def': [], 'up': 1, 'down': 0}]}}))
        with patch.object(aiorequests, 'post', AsyncMock(return_value=response)) as post:
            entries = asyncio.run(arena.do_query([1001, 1002, 1003, 1004, 1005], 301, 2))
        self.assertEqual(post.await_args.kwargs['json']['region'], 2)
        self.assertEqual(post.await_args.kwargs['json']['def'][0], 100101)
        self.assertEqual(entries[0]['atk'][0].id, 1001)
        self.assertEqual(len(entries[0]['qkey']), 5)

    def test_arena_render_interface(self):
        from PIL import Image, ImageFont
        from hoshino import R
        from hoshino.modules.priconne import arena
        icon = Image.new('RGBA', (64, 64), 'white')
        entry = {'atk': [types.SimpleNamespace(render_icon=AsyncMock(return_value=icon))],
                 'qkey': 'ABCDE', 'user_like': 0, 'up': 1, 'down': 0,
                 'my_up': 0, 'my_down': 0}
        font = ImageFont.load_default()
        with patch.object(R.ResImg, 'open', return_value=icon), patch.object(
                ImageFont, 'truetype', return_value=font):
            result = asyncio.run(arena.render_atk_def_teams([entry]))
        self.assertEqual(result.size, (420, 64))

    @contextmanager
    def _recognition_library(self, count=5):
        import numpy as np
        from hoshino.modules.priconne.arena import old_main
        library = {uid * 100 + 31: np.random.default_rng(uid).integers(
            40, 180, size=(128, 128, 3), dtype=np.uint8)
            for uid in range(1001, 1001 + count)}
        with patch.multiple(old_main, data=library, data_processed=None, _data_loaded=True):
            yield old_main, library

    def test_recognition_empty_library(self):
        from PIL import Image
        with self._recognition_library(0) as (recognition, _):
            self.assertEqual(asyncio.run(recognition.getUnit(Image.new('RGB', (128, 128)))),
                             (0, 0, 'Unknown', -1))
            self.assertEqual(asyncio.run(recognition.getBox(Image.new('RGB', (400, 200)))),
                             ([], ''))

    def test_recognition_single_avatar_and_hash_distance(self):
        from PIL import Image
        import numpy as np
        with self._recognition_library() as (recognition, library):
            image = Image.fromarray(library[100131])
            result = asyncio.run(recognition.getUnit(image))
            self.assertEqual(result[:2], (100131, 1001))
            self.assertEqual(result[-1], 100)
            self.assertEqual(asyncio.run(recognition.calc_distance_img(image, image)), 0)
            left, right = np.zeros((16, 16), dtype=np.uint8), np.ones((16, 16), dtype=np.uint8)
            self.assertEqual(asyncio.run(recognition.calc_distance_arr(left, right)), 256)

    def test_recognition_five_avatar_screenshot(self):
        from PIL import Image, ImageDraw
        with self._recognition_library() as (recognition, library):
            screenshot = Image.new('RGB', (730, 180), 'black')
            draw = ImageDraw.Draw(screenshot)
            for index, (_, pixels) in enumerate(library.items()):
                x, y = 10 + index * 140, 20
                draw.rectangle((x, y, x + 131, y + 131), fill='white')
                screenshot.paste(Image.fromarray(pixels), (x + 2, y + 2))
            teams, preview = asyncio.run(recognition.getBox(screenshot))
        self.assertEqual(teams, [[1005, 1004, 1003, 1002, 1001]])
        self.assertEqual(preview.count('[CQ:image,file=base64://'), 2)

    def test_recognition_four_avatar_screenshot(self):
        from PIL import Image, ImageDraw
        with self._recognition_library(4) as (recognition, library):
            screenshot = Image.new('RGB', (590, 180), 'black')
            draw = ImageDraw.Draw(screenshot)
            for index, (_, pixels) in enumerate(library.items()):
                x, y = 10 + index * 140, 20
                draw.rectangle((x, y, x + 131, y + 131), fill='white')
                screenshot.paste(Image.fromarray(pixels), (x + 2, y + 2))
            teams, _ = asyncio.run(recognition.getBox(screenshot))
        self.assertEqual(teams, [[1004, 1003, 1002, 1001]])

    def test_recognition_build_local_library(self):
        from PIL import Image
        from hoshino import R
        from hoshino.modules.priconne.arena import record, old_main
        target = Path(R.img('priconne/unit/icon_unit_100131.png').path)
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (128, 128), 'red').save(target)
        cache_root = Path(self.temp.name) / 'recognition-cache'
        cache_root.mkdir()
        with patch.object(record, '__file__', str(cache_root / 'record.py')), patch.multiple(
                old_main, data={}, data_processed=None, _data_loaded=False):
            message = record.update_dic()
            self.assertIn('1个', message)
            self.assertIn(100131, old_main.data)
            self.assertTrue((cache_root / 'dic.npy').exists())
            result = asyncio.run(old_main.getUnit(Image.new('RGB', (128, 128), 'red')))
            self.assertEqual(result[:2], (100131, 1001))

    def test_recognition_two_team_screenshot(self):
        from PIL import Image, ImageDraw
        with self._recognition_library() as (recognition, library):
            screenshot = Image.new('RGB', (730, 340), 'black')
            draw = ImageDraw.Draw(screenshot)
            for row, pixels_list in enumerate((list(library.values()), list(library.values())[::-1])):
                for index, pixels in enumerate(pixels_list):
                    x, y = 10 + index * 140, 20 + row * 160
                    draw.rectangle((x, y, x + 131, y + 131), fill='white')
                    screenshot.paste(Image.fromarray(pixels), (x + 2, y + 2))
            teams, _ = asyncio.run(recognition.getBox(screenshot))
        self.assertEqual(teams, [[1005, 1004, 1003, 1002, 1001], [1001, 1002, 1003, 1004, 1005]])

    def test_arena_image_command_routes_to_recognition(self):
        from hoshino.typing import Message
        from hoshino.modules.priconne.arena import old_main
        message = Message([{'type': 'text', 'data': {'text': '日怎么拆'}},
                           {'type': 'image', 'data': {'file': 'lineup.png',
                            'url': 'https://example.invalid/lineup.png'}}])
        with patch.object(old_main, '_QueryArenaImageAsync', AsyncMock()) as recognize:
            self._query(message, 302)
        recognize.assert_awaited_once()
        self.assertEqual(recognize.await_args.args[:2], ('https://example.invalid/lineup.png', 4))


if __name__ == '__main__':
    unittest.main()
