"""Startup notification regressions; all delivery is mocked."""
import contextlib
import io
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import notify
import server


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_phone_links_without_unlock_secrets(self):
        text, host, play = notify._push_message({
            'url': 'https://game.example/?host=secret#private', 'code': 'ABCD',
            'host_you': 'https://game.example/host?host=secret',
            'source': 'my-computer', 'public': False,
        })
        self.assertEqual(host, 'https://game.example/host/play')
        self.assertEqual(play, 'https://game.example/ABCD')
        for expected in ('my-computer', 'same WiFi', 'ABCD', host, play):
            self.assertIn(expected, text)
        self.assertNotIn('secret', text)
        self.assertNotIn('private', text)

    def test_push_headers_and_verified_tls(self):
        os.environ.update(NOTIFY_TOKEN='private-token', SMTP_INSECURE='1')
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'ok'
        with patch.object(notify, '_ssl_context') as tls, patch.object(notify.urllib.request, 'urlopen', return_value=response) as send:
            self.assertEqual(notify._send_webhook('https://ntfy.sh/topic', 'hello', 'https://game/host/play', 'https://game/ABCD'), b'ok')
        tls.assert_called_once_with(allow_insecure=False)
        request = send.call_args.args[0]
        self.assertEqual(request.data, b'hello')
        self.assertEqual(request.get_header('Authorization'), 'Bearer private-token')
        self.assertEqual(request.get_header('Click'), 'https://game/host/play')
        self.assertIn('Join game, https://game/ABCD', request.get_header('Actions'))

    def test_retry_temporary_failure(self):
        response = MagicMock()
        with patch.object(notify, '_ssl_context'), patch.object(notify.time, 'sleep') as sleep, patch.object(notify.urllib.request, 'urlopen', side_effect=[URLError('offline'), response]) as send:
            notify._send_webhook('https://ntfy.sh/topic', 'hello')
        self.assertEqual(send.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_permanent_failure_is_not_retried_or_leaked(self):
        os.environ['NOTIFY_WEBHOOK'] = 'https://ntfy.sh/private-topic'
        output = io.StringIO()
        error = HTTPError(os.environ['NOTIFY_WEBHOOK'], 403, 'private-token', {}, None)
        with patch.object(notify, '_ssl_context'), patch.object(notify.time, 'sleep') as sleep, patch.object(notify.urllib.request, 'urlopen', side_effect=error) as send, contextlib.redirect_stdout(output):
            sent, failed = notify._run({'url': 'https://game.example', 'code': 'ABCD'})
        self.assertEqual(sent, [])
        self.assertTrue(failed)
        self.assertEqual(send.call_count, 1)
        sleep.assert_not_called()
        self.assertIn('HTTP 403', output.getvalue())
        self.assertNotIn('private-topic', output.getvalue())
        self.assertNotIn('private-token', output.getvalue())

    def test_unconfigured_is_noop_and_configured_is_background(self):
        with patch.object(notify.threading, 'Thread') as thread:
            self.assertIsNone(notify.notify_server_started({}))
            thread.assert_not_called()
            os.environ['NOTIFY_WEBHOOK'] = 'https://ntfy.sh/topic'
            worker = notify.notify_server_started({'url': 'https://game'})
            self.assertTrue(thread.call_args.kwargs['daemon'])
            worker.start.assert_called_once()

    def test_final_startup_url_and_deduplication(self):
        for public_url, url in [('', 'http://192.168.1.2:8080'), ('https://fixed.example', 'https://fixed.example'), ('https://random.trycloudflare.com', 'https://random.trycloudflare.com')]:
            with self.subTest(url=url), patch.object(server, 'PUBLIC_URL', public_url), patch.object(server, 'base_url', return_value=url), patch.object(server, '_notified_startup_urls', set()), patch.object(notify, 'notify_server_started') as send, contextlib.redirect_stdout(io.StringIO()):
                server._banner(bool(public_url))
                server._banner(bool(public_url))
                send.assert_called_once()
                links = send.call_args.args[0]
                self.assertEqual(links['url'], url)
                self.assertEqual(links['public'], bool(public_url))
                self.assertEqual(links['code'], server.ROOM_CODE)


if __name__ == '__main__':
    unittest.main()
