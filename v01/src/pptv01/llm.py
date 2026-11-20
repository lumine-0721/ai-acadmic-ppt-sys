"""OpenAI-compatible chat client + JSON helper with one repair retry.

Configuration comes only from environment variables:

  PPTV01_LLM_BASE_URL   e.g. https://api.example.com/v1   (required)
  PPTV01_LLM_API_KEY    secret key                         (required)
  PPTV01_LLM_MODEL      model id                           (required)
  PPTV01_LLM_TIMEOUT_S  request timeout, default 120       (optional)
  PPTV01_LLM_TEMPERATURE default 0.3                       (optional)
  PPTV01_LLM_JSON_MODE  "1" to send response_format=json_object (optional, default off)

Alternative backend (clearly labelled, NOT an API model):

  PPTV01_LLM_BACKEND=agent  plus  PPTV01_AGENT_RESPONSES_DIR=<dir>
  The client writes every prompt to <dir>/<stage>_request_<n>.json and reads the
  answer from <dir>/<stage>_response_<n>.json (written by a human/agent acting
  as the model). If the response file is missing it raises AwaitingAgentResponse.
  The answer goes through exactly the same JSON parsing/validation/post-checks.

There is deliberately NO mock/fallback here: when configuration is missing a
real run fails with LLMConfigError. Test doubles live under tests/ only.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, TypeVar
from urllib.parse import urlparse

import httpx

ENV_BASE_URL = "PPTV01_LLM_BASE_URL"
ENV_API_KEY = "PPTV01_LLM_API_KEY"
ENV_MODEL = "PPTV01_LLM_MODEL"
REQUIRED_ENV = (ENV_BASE_URL, ENV_API_KEY, ENV_MODEL)

T = TypeVar("T")


class LLMError(RuntimeError):
    pass


class LLMConfigError(LLMError):
    """Required LLM environment variables are missing."""


class LLMRequestError(LLMError):
    """The HTTP call failed or the response was not a chat completion."""


class AwaitingAgentResponse(Exception):
    """Agent backend: the prompt was written, the response file does not exist yet.

    Deliberately NOT an LLMError: it is not a failure, the run must be resumed."""

    def __init__(self, request_path: Path, response_path: Path):
        super().__init__(
            f"awaiting agent response: read {request_path} and write the answer to {response_path}, "
            "then re-run with --resume"
        )
        self.request_path = request_path
        self.response_path = response_path


class LLMOutputError(LLMError):
    """The model output could not be parsed/validated even after one repair retry."""

    def __init__(self, message: str, attempts: list[dict[str, Any]]):
        super().__init__(message)
        self.attempts = attempts


Messages = list[dict[str, str]]


class ChatClient(Protocol):
    model_id: str

    def chat(self, messages: Messages) -> str: ...

    def set_stage(self, stage: str) -> None: ...

    def describe(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str = field(repr=False)
    model: str
    timeout_s: float = 120.0
    temperature: float = 0.3
    json_mode: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LLMConfig":
        env = os.environ if env is None else env
        missing = [k for k in REQUIRED_ENV if not env.get(k, "").strip()]
        if missing:
            raise LLMConfigError(
                "LLM is not configured; set environment variable(s): "
                + ", ".join(missing)
                + ". A real run never falls back to mock output."
            )
        try:
            timeout = float(env.get("PPTV01_LLM_TIMEOUT_S", "120"))
            temp = float(env.get("PPTV01_LLM_TEMPERATURE", "0.3"))
        except ValueError as e:
            raise LLMConfigError(f"invalid numeric LLM setting: {e}") from None
        return cls(
            base_url=env[ENV_BASE_URL].strip().rstrip("/"),
            api_key=env[ENV_API_KEY].strip(),
            model=env[ENV_MODEL].strip(),
            timeout_s=timeout,
            temperature=temp,
            json_mode=env.get("PPTV01_LLM_JSON_MODE", "").strip() == "1",
        )


class OpenAICompatClient:
    """Minimal /chat/completions client. Never logs or stores the API key."""

    def __init__(self, config: LLMConfig, transport: httpx.BaseTransport | None = None):
        self._cfg = config
        self.model_id = config.model
        self._http = httpx.Client(timeout=config.timeout_s, transport=transport)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "OpenAICompatClient":
        return cls(LLMConfig.from_env(env))

    def describe(self) -> dict[str, Any]:
        host = urlparse(self._cfg.base_url).hostname or "unknown"
        return {
            "backend": "api",
            "provider_host": host,
            "model": self.model_id,
            "temperature": self._cfg.temperature,
            "json_mode": self._cfg.json_mode,
        }

    def set_stage(self, stage: str) -> None:  # API backend does not care about stages
        pass

    def chat(self, messages: Messages) -> str:
        body: dict[str, Any] = {
            "model": self._cfg.model,
            "messages": messages,
            "temperature": self._cfg.temperature,
        }
        if self._cfg.json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            r = self._http.post(
                f"{self._cfg.base_url}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self._cfg.api_key}"},
            )
        except httpx.HTTPError as e:
            raise LLMRequestError(f"LLM request failed: {type(e).__name__}") from None
        if r.status_code != 200:
            # Do not echo request headers; truncate body.
            raise LLMRequestError(f"LLM HTTP {r.status_code}: {r.text[:300]}")
        try:
            data = r.json()
            return data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMRequestError("LLM response was not a valid chat completion") from None

    def close(self) -> None:
        self._http.close()


AGENT_MODEL_ID = "agent:宝特 (manual, not an API model)"
ENV_BACKEND = "PPTV01_LLM_BACKEND"
ENV_AGENT_DIR = "PPTV01_AGENT_RESPONSES_DIR"


class AgentClient:
    """File-exchange backend: a human/agent acts as the model.

    *** This is a real answerer (not a mock) but NOT an API model; it is labelled
    as such in run_report.json. Its output is validated like any other. ***
    """

    model_id = AGENT_MODEL_ID

    def __init__(self, exchange_dir: Path):
        self.dir = Path(exchange_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._stage = "X"
        self._n: dict[str, int] = {}

    def set_stage(self, stage: str) -> None:
        self._stage = stage
        self._n[stage] = 0

    def describe(self) -> dict[str, Any]:
        return {"backend": "agent", "model": self.model_id, "exchange_dir": self.dir.name}

    def chat(self, messages: Messages) -> str:
        stage = self._stage
        n = self._n[stage] = self._n.get(stage, 0) + 1
        req = self.dir / f"{stage}_request_{n}.json"
        resp = self.dir / f"{stage}_response_{n}.json"
        req.write_text(
            json.dumps({"stage": stage, "n": n, "messages": messages}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if not resp.is_file():
            raise AwaitingAgentResponse(req, resp)
        return resp.read_text(encoding="utf-8")


def client_from_env(env: Mapping[str, str] | None = None) -> ChatClient:
    """Build the configured client. Backend 'api' (default) or 'agent'."""
    env = os.environ if env is None else env
    backend = env.get(ENV_BACKEND, "api").strip().lower() or "api"
    if backend == "api":
        return OpenAICompatClient.from_env(env)
    if backend == "agent":
        d = env.get(ENV_AGENT_DIR, "").strip()
        if not d:
            raise LLMConfigError(f"{ENV_BACKEND}=agent requires {ENV_AGENT_DIR}")
        return AgentClient(Path(d))
    raise LLMConfigError(f"unknown {ENV_BACKEND}={backend!r}; use 'api' or 'agent'")


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)


def extract_json(text: str) -> Any:
    """Parse a JSON value from model text (tolerates code fences / surrounding prose)."""
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    m = _FENCE.search(text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except ValueError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("no JSON object found in model output")


@dataclass
class JSONResult:
    value: Any
    attempts: list[dict[str, Any]]  # trace: raw output + error per attempt (no secrets)


def complete_json(
    client: ChatClient,
    messages: Messages,
    parse: Callable[[Any], T],
) -> JSONResult:
    """Ask for JSON, run ``parse`` (pydantic validation + post-validation) and
    retry exactly once with the error fed back. ``parse`` raises ValueError
    (pydantic.ValidationError is one) when the output is unacceptable."""
    attempts: list[dict[str, Any]] = []
    convo = list(messages)
    for attempt in (1, 2):
        raw = client.chat(convo)
        try:
            value = parse(extract_json(raw))
        except ValueError as e:
            attempts.append({"attempt": attempt, "raw": raw, "error": str(e)})
            if attempt == 2:
                raise LLMOutputError(
                    "model output invalid after one repair retry: " + str(e)[:500], attempts
                ) from None
            convo = convo + [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        "上一次输出未通过校验 / Your previous output failed validation:\n"
                        f"{str(e)[:2000]}\n\n"
                        "请只输出修正后的完整 JSON 对象，不要任何解释。 "
                        "Reply with the corrected full JSON object only."
                    ),
                },
            ]
            continue
        attempts.append({"attempt": attempt, "raw": raw, "error": None})
        return JSONResult(value=value, attempts=attempts)
    raise AssertionError("unreachable")  # pragma: no cover
