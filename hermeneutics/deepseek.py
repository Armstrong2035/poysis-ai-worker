"""Direct DeepSeek model transport using a server-injected API key."""

import json
import os

import httpx


class DeepSeekError(RuntimeError):
    """Safe to expose without provider bodies, prompts or credentials."""


class DeepSeekModel:
    def __init__(self, *, transport=None):
        self._key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not self._key:
            raise DeepSeekError("DEEPSEEK_API_KEY is not configured in the server runtime")
        self.model_version = os.getenv("HERMENEUTICS_DEEPSEEK_MODEL", "deepseek-v4-pro")
        self.transport = transport

    async def complete(self, prompt):
        # Fixed provider URL: client input cannot redirect credentials elsewhere.
        try:
            async with httpx.AsyncClient(timeout=180, transport=self.transport, follow_redirects=False) as client:
                response = await client.post("https://api.deepseek.com/chat/completions", headers={
                    "Authorization": "Bearer " + self._key,
                }, json={"model": self.model_version, "messages": [{"role": "user", "content": prompt}],
                         "response_format": {"type": "json_object"}, "max_tokens": 16000})
            if response.status_code != 200:
                raise DeepSeekError(f"DeepSeek request failed (HTTP {response.status_code})")
            body = response.json()
            choice = body["choices"][0]
            content = choice["message"].get("content")
            if choice.get("finish_reason") != "stop" or not isinstance(content, str) or not content.strip():
                raise DeepSeekError("DeepSeek returned an incomplete response")
            if len(content) > 60000:
                raise DeepSeekError("DeepSeek response exceeded the interpretation limit")
            json.loads(content)
            return content
        except DeepSeekError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            raise DeepSeekError("DeepSeek request failed or returned invalid JSON") from None
