import json
import os
import unittest
from unittest.mock import patch

import httpx

from hermeneutics.deepseek import DeepSeekError, DeepSeekModel


class DeepSeekTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_provider_and_json_mode(self):
        def handler(request):
            self.assertEqual(str(request.url), "https://api.deepseek.com/chat/completions")
            self.assertEqual(request.headers["Authorization"], "Bearer test-only")
            payload = json.loads(request.content)
            self.assertEqual(payload["response_format"], {"type": "json_object"})
            self.assertEqual(payload["model"], "deepseek-v4-pro")
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]})
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-only"}, clear=True):
            model = DeepSeekModel(transport=httpx.MockTransport(handler))
            self.assertEqual(json.loads(await model.complete("Return JSON")), {"ok": True})

    async def test_failure_does_not_expose_response(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-only"}, clear=True):
            model = DeepSeekModel(transport=httpx.MockTransport(lambda request: httpx.Response(401, text="private provider body")))
            with self.assertRaises(DeepSeekError) as error:
                await model.complete("private prompt")
            self.assertNotIn("private", str(error.exception))
            self.assertNotIn("test-only", str(error.exception))

    async def test_truncated_json_rejected(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-only"}, clear=True):
            model = DeepSeekModel(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
                "choices": [{"finish_reason": "length", "message": {"content": "{}"}}]})))
            with self.assertRaises(DeepSeekError):
                await model.complete("Return JSON")

    def test_missing_key_fails_without_fallback(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(DeepSeekError):
            DeepSeekModel()
