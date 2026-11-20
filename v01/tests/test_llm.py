import json

import httpx
import pytest

from pptv01.llm import (
    LLMConfig, LLMConfigError, LLMOutputError, LLMRequestError, OpenAICompatClient, complete_json, extract_json,
)
from fakes import FakeClient

ENV = {"PPTV01_LLM_BASE_URL": "https://llm.example/v1/", "PPTV01_LLM_API_KEY": "sk-secret-123", "PPTV01_LLM_MODEL": "m-1"}


def test_missing_env_raises_clear_error():
    with pytest.raises(LLMConfigError) as e:
        LLMConfig.from_env({})
    msg = str(e.value)
    for k in ENV:
        assert k in msg
    with pytest.raises(LLMConfigError, match="PPTV01_LLM_API_KEY"):
        LLMConfig.from_env({k: v for k, v in ENV.items() if k != "PPTV01_LLM_API_KEY"})


def test_blank_env_counts_as_missing():
    with pytest.raises(LLMConfigError):
        LLMConfig.from_env({**ENV, "PPTV01_LLM_MODEL": "  "})


def test_key_never_in_repr_or_describe():
    cfg = LLMConfig.from_env(ENV)
    assert "sk-secret-123" not in repr(cfg)
    client = OpenAICompatClient(cfg)
    assert "sk-secret-123" not in json.dumps(client.describe())
    assert client.model_id == "m-1" and client.describe()["provider_host"] == "llm.example"


def test_http_client_request_shape_and_errors():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url); seen["auth"] = req.headers["authorization"]; seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{\"a\": 1}"}}]})

    c = OpenAICompatClient(LLMConfig.from_env(ENV), transport=httpx.MockTransport(handler))
    assert c.chat([{"role": "user", "content": "hi"}]) == '{"a": 1}'
    assert seen["url"] == "https://llm.example/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-secret-123" and seen["body"]["model"] == "m-1"
    assert "response_format" not in seen["body"]

    bad = OpenAICompatClient(LLMConfig.from_env(ENV), transport=httpx.MockTransport(lambda r: httpx.Response(500, text="boom")))
    with pytest.raises(LLMRequestError, match="500") as e:
        bad.chat([])
    assert "sk-secret-123" not in str(e.value)


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('text ```json\n{"a": 2}\n``` more') == {"a": 2}
    assert extract_json('prefix {"a": 3} suffix') == {"a": 3}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_complete_json_one_repair_retry():
    def parse(raw):
        if "ok" not in raw:
            raise ValueError("missing ok")
        return raw
    c = FakeClient(["not json", {"ok": True}])
    res = complete_json(c, [{"role": "user", "content": "x"}], parse)
    assert res.value == {"ok": True} and len(res.attempts) == 2
    c = FakeClient([{"no": 1}, {"no": 2}, {"ok": 1}])
    with pytest.raises(LLMOutputError) as e:
        complete_json(c, [], parse)
    assert len(c.calls) == 2 and len(e.value.attempts) == 2


def test_no_fake_client_in_package():
    import pkgutil, pptv01
    names = [m.name for m in pkgutil.walk_packages(pptv01.__path__, "pptv01.")]
    assert not [n for n in names if "fake" in n.lower() or "mock" in n.lower()]
