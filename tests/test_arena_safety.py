"""Offline regression checks for arena errors and resource-free replies."""
import asyncio
import copy
import importlib.util
import sys
import types
import tempfile
from pathlib import Path
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

    def test_malformed_response_rejected(self):
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
        for response in variants:
            with self.subTest(response=response):
                with self.assertRaises(arena.ArenaResponseError):
                    arena._validate_results(response)

    def test_http_failure_and_invalid_json_do_not_enter_cache(self):
        from hoshino import aiorequests
        from hoshino.modules.priconne.arena import arena
        for response, error in (
            (types.SimpleNamespace(raise_for_status=Mock(side_effect=
                aiorequests.HTTPError('HTTP 503')), json=AsyncMock()), aiorequests.HTTPError),
            (types.SimpleNamespace(raise_for_status=Mock(), json=AsyncMock(
                side_effect=ValueError('HTML instead of JSON'))), ValueError),
        ):
            response.raw_response = Mock()
            with tempfile.TemporaryDirectory() as cache, patch.object(arena, '_gs_data_dir', Path(cache)), \
                    patch.object(aiorequests, 'post', AsyncMock(return_value=response)):
                with self.assertRaises(error):
                    asyncio.run(arena.do_query([1001, 1002, 1003, 1004, 1005]))
                self.assertEqual(list(Path(cache).iterdir()), [])
                response.raw_response.close.assert_called_once()

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
                patch.object(arena.old_main, 'query_candidates', query), \
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

    def test_no_candidates_gives_user_a_failure_reply(self):
        bot = self._command([])
        self.assertIn('未查询到解法', bot.finish.await_args.args[1])

    def test_followup_screenshot_stays_in_original_group(self):
        from hoshino.modules.priconne import arena
        from hoshino.typing import CQEvent, Message
        async def exercise():
            bot = types.SimpleNamespace(send=AsyncMock())
            ev = CQEvent({'self_id': 1, 'user_id': 2, 'group_id': 3, 'message': Message('')})
            await arena._arena_query(bot, ev, 4, _skip_limiter=True)
            ev.message = Message([{'type': 'image', 'data': {'url': 'https://example.invalid/team.png'}}])
            with patch.object(arena.old_main, '_QueryArenaImageAsync', AsyncMock()) as recognize:
                await arena.pending_screenshot(bot, CQEvent(dict(ev, group_id=4)))
                recognize.assert_not_awaited()
                await arena.pending_screenshot(bot, ev)
                await arena.pending_screenshot(bot, ev)
                recognize.assert_awaited_once_with('https://example.invalid/team.png', 4, bot, ev)
        asyncio.run(exercise())

    def test_unimplemented_commands_are_not_advertised_or_registered(self):
        from hoshino.modules.priconne import arena
        self.assertNotIn('点赞', arena.sv_help)
        self.assertNotIn('点踩', arena.sv_help)
        self.assertFalse(hasattr(arena, 'upload'))

    def test_multi_team_screenshot_combines_disjoint_candidates(self):
        from hoshino.modules.priconne import arena, chara
        recognition = arena.old_main
        defense = [[1001, 1002, 1003, 1004, 1005], [1006, 1007, 1008, 1009, 1010]]
        attacks = [[1011, 1012, 1013, 1014, 1015], [1016, 1017, 1018, 1019, 1020]]
        results = [[{'atk': [chara.fromid(uid) for uid in team], 'val': 1}] for team in attacks]
        bot = types.SimpleNamespace(send=AsyncMock(), finish=AsyncMock(
            side_effect=self.hoshino.CanceledException('test reply')))
        with patch.object(chara, 'is_npc', return_value=False), \
                patch.object(recognition, 'get_pic', AsyncMock(return_value=b'mock')), \
                patch.object(recognition, '_recognize_bytes', AsyncMock(return_value=(defense, ''))), \
                patch.object(recognition, 'load_best_attacks', return_value=[]), \
                patch.object(recognition, 'query_candidates', AsyncMock(side_effect=results)) as query, \
                patch.object(recognition, 'format_results', AsyncMock(return_value='result')) as render:
            with self.assertRaises(self.hoshino.CanceledException):
                asyncio.run(recognition._QueryArenaImageAsync('https://example.invalid/team.png', 2, bot, object()))
        self.assertEqual([call.args[:2] for call in query.await_args_list], [(team, 2) for team in defense])
        rows, actual_defense = render.await_args.args
        self.assertEqual(actual_defense, defense)
        self.assertEqual([[c.id for c in row['atk']] for row in rows], attacks)

    @unittest.skipUnless(importlib.util.find_spec('onnxruntime'), 'optional ONNX runtime not installed')
    def test_bundled_onnx_candidates_pass_to_mzq(self):
        from hoshino.modules.priconne import chara
        from hoshino.modules.priconne.arena.model_backend import ModelSettings, OnnxGenerator
        from hoshino.modules.priconne.arena.query_provider import ArenaModelProvider
        from hoshino.modules.priconne.arena.mzq_client import build_request, parse_result
        settings = ModelSettings(candidate_count=4, simulate_count=2, repeats=1)
        model = OnnxGenerator(settings)
        defense = tuple(uid // 100 for uid in model.units[:5])

        def simulate(pairs, manifest, repeats):
            parsed = {}
            for pair in pairs:
                request = build_request(*pair, manifest, repeats)
                self.assertEqual({u['unitId'] for u in request['EnemyList']}, set(pair[1]))
                self.assertEqual({u['unitId'] for u in request['PlayerList']}, set(pair[0]))
                parsed[pair] = parse_result({'Success': True, 'Result': 0,
                                             'BattleFrames': 600, 'CurrentSeed': 42}, repeats)
            self.assertTrue(parsed)
            return parsed, {}

        client = types.SimpleNamespace(simulate=Mock(side_effect=simulate))
        provider = ArenaModelProvider(settings, model_factory=lambda _: model, client_factory=lambda *_: client)
        rows = asyncio.run(provider.augment(defense, 1, [], chara.fromid))
        client.simulate.assert_called_once()
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(rows[0]['simulation']['seconds'], 10)
        self.assertTrue(all(len({c.id for c in row['atk']}) == 5 for row in rows))


if __name__ == '__main__':
    unittest.main()
