from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class ModelRequest:
    prompt: str
    files: Dict[str, str]
    schema: Optional[Dict[str, object]]


@dataclass(frozen=True)
class ModelResult:
    text: str
    adapter: str
    remote: bool


class ModelAdapter:
    name = "base"
    remote = False

    def complete(self, request: ModelRequest) -> ModelResult:
        raise NotImplementedError


class DryRunAdapter(ModelAdapter):
    name = "dry-run"
    remote = False

    def complete(self, request: ModelRequest) -> ModelResult:
        first_file = next(iter(request.files.values()), "")
        excerpt = first_file.strip().replace("\n", " ")[:240]
        return ModelResult(
            text=f"Dry-run response for prompt '{request.prompt}': {excerpt}",
            adapter=self.name,
            remote=self.remote,
        )


class OllamaAdapter(ModelAdapter):
    name = "ollama"
    remote = False

    def __init__(self, model: str = "llama3.1", base_url: str = "http://localhost:11434") -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")

    def complete(self, request: ModelRequest) -> ModelResult:
        payload = json.dumps(
            {"model": self.model, "prompt": request.prompt, "stream": False}
        ).encode("utf-8")
        http_request = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=60) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Ollama request failed: {exc}") from exc
        return ModelResult(text=str(body.get("response", "")), adapter=self.name, remote=False)


class OpenAICompatibleAdapter(ModelAdapter):
    name = "openai-compatible"
    remote = True

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key_env: str = "OPENAI_API_KEY",
        enabled: bool = False,
    ) -> None:
        if not enabled:
            raise RuntimeError("Remote adapters are disabled unless explicitly configured")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env

    def complete(self, request: ModelRequest) -> ModelResult:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(f"Missing API key in {self.api_key_env}")
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": request.prompt}],
            }
        ).encode("utf-8")
        http_request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=60) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"OpenAI-compatible request failed: {exc}") from exc
        content = body["choices"][0]["message"]["content"]
        return ModelResult(text=str(content), adapter=self.name, remote=True)
