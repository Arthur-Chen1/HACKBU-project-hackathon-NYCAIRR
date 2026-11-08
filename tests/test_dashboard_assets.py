"""Bird-guide routes coexist with the existing dashboard API without exposing files."""

import json
from unittest import TestCase
from unittest.mock import Mock

from src.dashboard.server import GUIDE_ASSETS, Handler, PAGE


class DashboardAssetTests(TestCase):
    def request(self, path):
        handler = Handler.__new__(Handler)
        handler.path = path
        handler._send = Mock()
        handler._error = Mock()
        handler.do_GET()
        return handler

    def test_guide_assets_are_served_with_correct_types(self):
        for path, mime in GUIDE_ASSETS.items():
            with self.subTest(path=path):
                handler = self.request(path)
                handler._error.assert_not_called()
                handler._send.assert_called_once_with(200, PAGE.with_name(path[1:]).read_bytes(), mime)
        self.assertTrue(PAGE.with_name("bird-guide-suit.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))

    def test_page_and_options_still_work(self):
        handler = self.request("/")
        handler._send.assert_called_once_with(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        handler = self.request("/api/options")
        status, payload = handler._send.call_args.args
        options = json.loads(payload)
        self.assertEqual(status, 200)
        self.assertIn("forecast_model_available", options)
        self.assertIn("grid", options)
        self.assertIn("manhattan", options["areas"])

    def test_unknown_files_and_api_errors_still_reject(self):
        for path in ("/server.py", "/../server.py", "/bird-guide-missing.png"):
            handler = self.request(path)
            handler._error.assert_called_once_with(404, "Not found")
        self.request("/api/job?id=missing")._error.assert_called_once_with(404, "Unknown job")
