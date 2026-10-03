"""Offline regression checks for arena errors and resource-free replies."""
import asyncio
import copy
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_minimal


class ArenaSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if 'hoshino' not in sys.modules:
            test_minimal.MinimalBotTests.setUpClass.__func__(cls)
        else:
            import hoshino
            cls.hoshino = hoshino
            cls.bot = hoshino.get_bot()

    cleanup_environment = classmethod(
        test_minimal.MinimalBotTests.cleanup_environment.__func__)

    def _response(self):
        return {'code': 0, 'data': {'result': [{
            'id': 'abcdef123456',
            'atk': [{'id': 100101, 'star': 3, 'equip': 0}],
            'def': [], 'up': 1, 'down': 0}]}}

    def test_malformed_response_rejected_before_creating_keys(self):
        from hoshino.modules.priconne.arena import arena
        valid = self._response()
        variants = [None, [], {}, {'code': '0'}, {'code': 0, 'data': None},
                    {'code': 0, 'data': {'result': {}}}]
        for field, value in [('id', 'not-hex'), ('atk', None), ('up', -1)]:
            item = copy.deepcopy(valid)
            item['data']['result'][0][field] = value
            variants.append(item)
        item = copy.deepcopy(valid)
        item['data']['result'][0]['atk'][0]['star'] = '3'
        variants.append(item)
        before = arena.quick_key_dic.copy()
        for response in variants:
            with self.subTest(response=response):
                with self.assertRaises(arena.ArenaResponseError):
                    arena._validate_results(response)
        self.assertEqual(arena.quick_key_dic, before)

    def test_http_failure_and_invalid_json_return_no_data(self):
        from hoshino import aiorequests
        from hoshino.modules.priconne.arena import arena
        for response in (
            types.SimpleNamespace(raise_for_status=Mock(side_effect=
                aiorequests.HTTPError('HTTP 503')), json=AsyncMock()),
            types.SimpleNamespace(raise_for_status=Mock(), json=AsyncMock(
                side_effect=ValueError('HTML instead of JSON'))),
        ):
            with patch.object(aiorequests, 'post', AsyncMock(return_value=response)):
                self.assertIsNone(asyncio.run(arena.do_query([1001] * 5, 901)))

    def _command(self, response, rendering_error=None):
        from hoshino.modules.priconne import arena, chara
        from hoshino.typing import CQEvent, Message
        ev = CQEvent({'user_id': 902, 'group_id': 1017321923,
                      'message': Message('测试阵容')})
        bot = types.SimpleNamespace(send=AsyncMock(), finish=AsyncMock(
            side_effect=self.hoshino.CanceledException('test reply')))
        query = AsyncMock(side_effect=response) if isinstance(response, Exception) \
            else AsyncMock(return_value=response)
        render = AsyncMock(side_effect=rendering_error)
        with patch.object(chara.roster, 'parse_team', return_value=(
                [1001, 1002, 1003, 1005, 1006], '')), \
                patch.object(arena.arena, 'do_query', query), \
                patch.object(arena, 'render_atk_def_teams', render):
            try:
                asyncio.run(arena._arena_query(bot, ev, 1, _skip_limiter=True))
            except self.hoshino.CanceledException:
                pass
        return bot

    def test_missing_resources_send_character_names_as_text(self):
        from hoshino.modules.priconne import chara
        entry = {'atk': [chara.fromid(1001, 3, 0)], 'up': 1, 'down': 0}
        bot = self._command([entry], OSError('missing font'))
        bot.finish.assert_not_awaited()
        self.assertIn(chara.fromid(1001).name, bot.send.await_args.args[1])
        self.assertIn('文字结果', bot.send.await_args.args[1])
        self.assertNotIn('[CQ:image', bot.send.await_args.args[1])

    def test_invalid_api_data_gives_user_a_failure_reply(self):
        from hoshino.modules.priconne.arena.arena import ArenaResponseError
        bot = self._command(ArenaResponseError('missing atk'))
        self.assertIn('无法解析', bot.finish.await_args.args[1])
        bot.send.assert_not_awaited()

    def test_unimplemented_commands_are_not_advertised_or_registered(self):
        from hoshino.modules.priconne import arena
        self.assertNotIn('点赞', arena.sv_help)
        self.assertNotIn('点踩', arena.sv_help)
        self.assertFalse(hasattr(arena, 'upload'))


if __name__ == '__main__':
    unittest.main()
