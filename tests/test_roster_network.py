"""Offline tests for GitHub roster retries and failure preservation."""
import asyncio
import base64
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch
import test_minimal


class RosterNetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if 'hoshino' not in sys.modules:
            test_minimal.MinimalBotTests.setUpClass.__func__(cls)

    cleanup_environment = classmethod(test_minimal.MinimalBotTests.cleanup_environment.__func__)

    def source(self):
        return "CHARA_NAME = {1000: ['Unknown'], 1001: ['Alias']}\nUnavailableChara = {1002}\nCHARA_PROFILE = {}\n"

    def response(self, source, api=False):
        from hoshino import aiorequests
        import requests
        response = requests.Response()
        response.status_code = 200
        response.encoding = 'utf8'
        if api:
            import json
            response._content = json.dumps({'encoding': 'base64',
                'content': base64.b64encode(source.encode()).decode()}).encode()
        else:
            response._content = source.encode()
        return aiorequests.AsyncResponse(response)

    def test_raw_reset_uses_official_api_without_sleep(self):
        from hoshino.modules.priconne import pcr_data_updater as updater
        api = self.response(self.source(), api=True)
        with patch.object(updater.aiorequests, 'get', AsyncMock(side_effect=[
                updater.aiorequests.ConnectionError('reset'), api])) as get, \
                patch.object(updater.asyncio, 'sleep', AsyncMock()) as sleep:
            self.assertEqual(asyncio.run(updater.fetch_roster_source()), self.source())
        self.assertEqual(get.await_args_list[1].args[0], updater.ROSTER_API_URL)
        self.assertEqual(get.await_args_list[0].kwargs['timeout'], (5, 15))
        sleep.assert_not_awaited()

    def test_both_endpoints_transient_failure_retry(self):
        from hoshino.modules.priconne import pcr_data_updater as updater
        with patch.object(updater.aiorequests, 'get', AsyncMock(side_effect=[
                updater.aiorequests.ConnectionError('reset'), updater.aiorequests.Timeout('timeout'),
                self.response(self.source())])) as get, \
                patch.object(updater.asyncio, 'sleep', AsyncMock()) as sleep:
            self.assertEqual(asyncio.run(updater.fetch_roster_source()), self.source())
        self.assertEqual(get.await_count, 3)
        sleep.assert_awaited_once_with(1)

    def test_exhausted_retries_do_not_install(self):
        from hoshino.modules.priconne import pcr_data_updater as updater
        sess = types.SimpleNamespace(send=AsyncMock())
        with patch.object(updater.aiorequests, 'get', AsyncMock(side_effect=
                updater.aiorequests.ConnectionError('reset'))) as get, \
                patch.object(updater.asyncio, 'sleep', AsyncMock()), \
                patch.object(updater, 'install_roster_source') as install:
            asyncio.run(updater.pull_chara(sess))
        self.assertEqual(get.await_count, 6)
        install.assert_not_called()
        sess.send.assert_awaited_once()
        self.assertIn('\u4fdd\u7559\u65e7\u82b1\u540d\u518c', sess.send.await_args.args[0])

    def test_invalid_source_never_installed(self):
        from hoshino.modules.priconne import pcr_data_updater as updater
        sess = types.SimpleNamespace(send=AsyncMock())
        with patch.object(updater.aiorequests, 'get', AsyncMock(return_value=
                self.response("X = __import__('os')"))), \
                patch.object(updater, 'install_roster_source') as install:
            asyncio.run(updater.pull_chara(sess))
        install.assert_not_called()
        sess.send.assert_awaited_once()
