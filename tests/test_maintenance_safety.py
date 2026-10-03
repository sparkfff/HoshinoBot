"""Regression checks for maintenance validation and shared plugin helpers."""
import asyncio
from datetime import datetime
from logging.handlers import RotatingFileHandler
import types
import unittest
from unittest.mock import AsyncMock, patch

import test_minimal


class MaintenanceSafetyTests(unittest.TestCase):
    setUpClass = classmethod(test_minimal.MinimalBotTests.setUpClass.__func__)
    cleanup_environment = classmethod(test_minimal.MinimalBotTests.cleanup_environment.__func__)

    def test_billing_rejects_all_invalid_pairs_before_sending(self):
        from hoshino.modules.botmanage.billing import billing

        for text in ('', '123', '123 2026-10-01 456', 'abc 2026-10-01',
                     '0 2026-10-01', '123 2026-02-29', '123 2026-13-01',
                     '123 2026-2-01', '123 2026-10-01 456 2026-02-30'):
            with self.subTest(text=text):
                bot = types.SimpleNamespace(get_group_list=AsyncMock(),
                                            send_group_msg=AsyncMock())
                session = types.SimpleNamespace(bot=bot, current_arg_text=text,
                                                finish=AsyncMock(), send=AsyncMock())
                asyncio.run(billing.__wrapped__(session))
                session.finish.assert_awaited_once()
                bot.get_group_list.assert_not_awaited()
                bot.send_group_msg.assert_not_awaited()

    def test_billing_accepts_leap_date(self):
        from hoshino.modules.botmanage.billing import billing

        bot = types.SimpleNamespace(
            get_group_list=AsyncMock(return_value=[{'group_id': 123}]),
            get_group_member_list=AsyncMock(return_value=[]),
            send_group_msg=AsyncMock())
        session = types.SimpleNamespace(bot=bot, current_arg_text='123 2028-02-29',
                                        finish=AsyncMock(), send=AsyncMock())
        with patch.object(self.hoshino, 'get_self_ids', return_value=[10000]):
            asyncio.run(billing.__wrapped__(session))
        session.finish.assert_not_awaited()
        bot.send_group_msg.assert_awaited_once()
        self.assertIn('2028-02-29', bot.send_group_msg.await_args.kwargs['message'])

    def test_daily_limit_resets_across_month_and_year(self):
        from hoshino.util import DailyNumberLimiter

        limiter = DailyNumberLimiter(1)
        for now in (datetime(2025, 12, 3, 6), datetime(2026, 1, 3, 6),
                    datetime(2026, 2, 3, 6)):
            with patch('hoshino.util.datetime') as clock:
                clock.now.return_value = now
                self.assertTrue(limiter.check('user'))
                limiter.increase('user')
                self.assertFalse(limiter.check('user'))

    def test_daily_limit_uses_five_am_boundary_for_every_operation(self):
        from hoshino.util import DailyNumberLimiter

        limiter = DailyNumberLimiter(1)
        with patch('hoshino.util.datetime') as clock:
            clock.now.return_value = datetime(2026, 10, 3, 4, 59)
            limiter.increase('user')
            self.assertFalse(limiter.check('user'))
            clock.now.return_value = datetime(2026, 10, 3, 5)
            self.assertEqual(limiter.get_num('user'), 0)
            limiter.increase('user')
            self.assertFalse(limiter.check('user'))

    def test_traditional_trigger_collision_preserves_handlers(self):
        from hoshino.trigger import PrefixTrigger, SuffixTrigger

        first, second = object(), object()
        for trigger_type in (PrefixTrigger, SuffixTrigger):
            with self.subTest(trigger=trigger_type.__name__):
                trigger = trigger_type()
                trigger.add('查詢', first)
                trigger.add('查询', second)
                trigger.add('查询', second)
                key = '查詢' if trigger_type is PrefixTrigger else '查詢'[::-1]
                self.assertEqual(trigger.trie[key], [first, second])
                other = '查询' if trigger_type is PrefixTrigger else '查询'[::-1]
                self.assertEqual(trigger.trie[other], [second])

    def test_error_logs_have_size_and_history_limits(self):
        from hoshino import log

        for handler in (log.error_handler, log.critical_handler):
            self.assertIsInstance(handler, RotatingFileHandler)
            self.assertEqual(handler.maxBytes, 10 * 1024 * 1024)
            self.assertEqual(handler.backupCount, 5)


if __name__ == '__main__':
    unittest.main()
