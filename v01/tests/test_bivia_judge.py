import json

import httpx

import pptv01.bivia_judge as bj
from pptv01.bivia_judge import BiviaJudge, bivia_enabled


def _handler(body, status=200):
    def handler(request):
        return httpx.Response(status, json=body, request=request)
    return handler


# --------------------------------------------------------------------------- #
# enablement / config
# --------------------------------------------------------------------------- #
def test_disabled_without_flag_or_key():
    assert BiviaJudge.from_env({}) is None
    # flag but no key
    assert BiviaJudge.from_env({"PPTV01_BIVIA_JUDGE": "1"}) is None
    # key but no flag
    assert BiviaJudge.from_env({"A8_API_KEY": "k"}) is None
    assert bivia_enabled({}) is False
    assert bivia_enabled({"PPTV01_BIVIA_JUDGE": "1", "A8_API_KEY": ""}) is False


def test_threshold_default_and_env_override():
    judge = BiviaJudge.from_env({"PPTV01_BIVIA_JUDGE": "1", "A8_API_KEY": "k"})
    assert judge is not None and judge.threshold == 0.90

    judge = BiviaJudge.from_env(
        {"PPTV01_BIVIA_JUDGE": "1", "A8_API_KEY": "k", "PPTV01_BIVIA_THRESHOLD": "0.5"}
    )
    assert judge is not None and judge.threshold == 0.5

    # invalid value falls back to the default
    judge = BiviaJudge.from_env(
        {"PPTV01_BIVIA_JUDGE": "1", "A8_API_KEY": "k", "PPTV01_BIVIA_THRESHOLD": "abc"}
    )
    assert judge is not None and judge.threshold == 0.90


# --------------------------------------------------------------------------- #
# judge_boolean outcomes
# --------------------------------------------------------------------------- #
def test_pass_on_yes_above_threshold():
    judge = BiviaJudge(
        "test-key",
        transport=httpx.MockTransport(
            _handler({"value": "yes", "probability": 0.95, "status": "answered"})
        ),
    )
    v = judge.judge_boolean("Is this slide accurate?")
    assert v.outcome == "pass"


def test_veto_on_no_above_threshold():
    judge = BiviaJudge(
        "test-key",
        transport=httpx.MockTransport(
            _handler({"value": "no", "probability": 0.97, "status": "answered"})
        ),
    )
    v = judge.judge_boolean("Is this slide accurate?")
    assert v.outcome == "veto"


def test_uncertain_on_low_probability():
    judge = BiviaJudge(
        "test-key",
        transport=httpx.MockTransport(
            _handler({"value": "yes", "probability": 0.5, "status": "answered"})
        ),
    )
    v = judge.judge_boolean("Q")
    assert v.outcome == "uncertain"
    assert v.reason == "low_confidence"


def test_uncertain_on_abstain():
    judge = BiviaJudge(
        "test-key",
        transport=httpx.MockTransport(
            _handler({"status": "abstained", "abstain": True, "value": None, "probability": None})
        ),
    )
    v = judge.judge_boolean("Q")
    assert v.outcome == "uncertain"
    assert v.reason == "abstained"


def test_not_run_on_network_error_never_raises():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    judge = BiviaJudge("test-key", transport=httpx.MockTransport(handler))
    v = judge.judge_boolean("Q")  # must not raise
    assert v.outcome == "not_run"


def test_not_run_on_non_2xx_or_unparsable():
    # non-2xx status
    judge = BiviaJudge(
        "test-key",
        transport=httpx.MockTransport(_handler({"error": "nope"}, status=422)),
    )
    assert judge.judge_boolean("Q").outcome == "not_run"

    # 200 but body is not JSON
    judge = BiviaJudge(
        "test-key",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"not json")),
    )
    assert judge.judge_boolean("Q").outcome == "not_run"

    # 200 but bad field type (string probability) -> bad_response
    judge = BiviaJudge(
        "test-key",
        transport=httpx.MockTransport(
            _handler({"value": "yes", "probability": "high", "status": "answered"})
        ),
    )
    v = judge.judge_boolean("Q")
    assert v.outcome == "not_run"
    assert v.reason == "bad_response"


def test_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setattr(bj, "_sleep", lambda s: None)  # never actually wait
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "rate limited"}, request=request)
        return httpx.Response(
            200, json={"value": "yes", "probability": 0.95, "status": "answered"}, request=request
        )

    judge = BiviaJudge("test-key", transport=httpx.MockTransport(handler))
    v = judge.judge_boolean("Q")
    assert v.outcome == "pass"
    assert calls["n"] == 2


# --------------------------------------------------------------------------- #
# repr safety
# --------------------------------------------------------------------------- #
def test_api_key_absent_from_repr():
    judge = BiviaJudge(
        "test-key",
        transport=httpx.MockTransport(
            _handler({"value": "yes", "probability": 0.95, "status": "answered"})
        ),
    )
    assert "test-key" not in repr(judge)


# --------------------------------------------------------------------------- #
# image veto
# --------------------------------------------------------------------------- #
def test_image_veto_maps_pass_to_not_vetoed():
    judge = BiviaJudge(
        "test-key",
        transport=httpx.MockTransport(_handler({"value": "yes", "probability": 0.99})),
    )
    assert judge.judge_image_veto("Q", "state", "aGVsbG8=").outcome == "not_vetoed"

    judge = BiviaJudge(
        "test-key",
        transport=httpx.MockTransport(_handler({"value": "no", "probability": 0.99})),
    )
    assert judge.judge_image_veto("Q", "state", "aGVsbG8=").outcome == "veto"


# --------------------------------------------------------------------------- #
# request shape
# --------------------------------------------------------------------------- #
def test_request_shape():
    captured = {}

    def handler(request):
        captured["req"] = request
        return httpx.Response(
            200, json={"value": "yes", "probability": 0.95, "status": "answered"}, request=request
        )

    judge = BiviaJudge("test-key", transport=httpx.MockTransport(handler))
    judge.judge_boolean("Is the sky blue?", state="ctx", mode="fast", think=False)

    req = captured["req"]
    assert req.url.path.endswith("/boolean")
    assert req.headers["authorization"] == "Bearer test-key"

    payload = json.loads(req.content)
    assert payload["question"] == "Is the sky blue?"
    assert payload["state"] == "ctx"
    assert payload["mode"] == "fast"
    assert payload["think"] is False
