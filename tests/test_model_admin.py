"""Offline tests: adding and choosing models, storing keys, and the OpenAI-compatible adapter
(against a mocked HTTP transport, no network)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
import pytest

from tagger import model_admin as admin
from tagger.config import ConfigError, load_settings
from tagger.providers import FatalError, Image, ModelRequest, RateLimited, RetryableError, SchemaError, get_provider
from tagger.providers import fake
from tagger.providers import openai_compatible as oc

ROOT = Path(__file__).parents[1]
GPT = dict(provider="openai_compatible", model_id="some-model", accepts_images=True, input_price=0.4,
           output_price=1.6, cached_input_price=0.1, api_key_env="TEST_OPENAI_KEY",
           base_url="https://api.example.test/v1")


@pytest.fixture
def cfg(tmp_path):
    d = tmp_path / "config"
    d.mkdir()
    for f in ("models.toml", "pipeline.toml"):
        shutil.copy(ROOT / "config" / f, d / f)
    return d


# ------------------------------------------------------------------ admin

def test_add_list_use_and_remove_a_model(cfg):
    m = admin.add_model(cfg, "gpt-mini", GPT)
    assert m["provider"] == "openai_compatible" and m["prices_checked"]
    text = (cfg / "models.toml").read_text()
    assert "Prices are USD per million tokens" in text and "[profiles.gpt-mini]" in text   # comments kept
    data = admin.list_models(cfg, env={"TEST_OPENAI_KEY": "sk-should-never-appear"})
    row = next(r for r in data["models"] if r["name"] == "gpt-mini")
    assert row["key_present"] and "sk-should-never-appear" not in json.dumps(data)   # never the key itself
    assert not next(r for r in admin.list_models(cfg, env={})["models"] if r["name"] == "gpt-mini")["key_present"]

    assert admin.set_default(cfg, "tag", "gpt-mini") == {"categorise": "haiku", "tag": "gpt-mini",
                                                         "golden_prefill": "opus"}
    assert admin.set_default(cfg, "categorise", "gpt-mini")["categorise"] == "gpt-mini"
    s = load_settings(cfg)
    assert s.tag_model == s.categorise_model == "gpt-mini" and s.profile("gpt-mini").base_url == GPT["base_url"]
    assert "category_auto_threshold" in (cfg / "pipeline.toml").read_text()

    with pytest.raises(admin.AdminError, match="default for"):
        admin.remove_model(cfg, "gpt-mini")
    admin.set_default(cfg, "tag", "sonnet")
    admin.set_default(cfg, "categorise", "haiku")
    assert admin.remove_model(cfg, "gpt-mini") == {"removed": "gpt-mini"}
    assert "gpt-mini" not in load_settings(cfg).profiles


def test_bad_profiles_are_rejected_with_reasons(cfg):
    with pytest.raises(admin.AdminError, match="already exists"):
        admin.add_model(cfg, "haiku", {**GPT})
    admin.add_model(cfg, "haiku", {**GPT}, replace=True)
    for bad, msg in ((dict(provider="nope"), "provider must be"), (dict(input_price=-1), "input_price"),
                     (dict(base_url=None), "needs base_url"), (dict(api_key_env="lower"), "api_key_env"),
                     (dict(json_mode="xml"), "json_mode")):
        with pytest.raises(admin.AdminError, match=msg):
            admin.add_model(cfg, "x1", {**GPT, **bad})
    with pytest.raises(admin.AdminError, match="name must be"):
        admin.add_model(cfg, "Bad Name", GPT)
    with pytest.raises(admin.AdminError, match="unknown fields"):
        admin.add_model(cfg, "x2", {**GPT, "temprature": 0})
    with pytest.raises(admin.AdminError, match="step must be"):
        admin.set_default(cfg, "derive", "haiku")
    with pytest.raises(admin.AdminError, match="valid names"):
        admin.set_default(cfg, "tag", "missing")


def test_unknown_fields_in_models_toml_stop_loading(cfg):
    (cfg / "models.toml").write_text((cfg / "models.toml").read_text() + '\n[profiles.x]\nprovider = "anthropic"\n'
                                     'model_id = "m"\naccepts_images = true\ninput_price = 1\noutput_price = 1\n'
                                     'colour = "red"\n')
    with pytest.raises(ConfigError, match="unknown fields"):
        load_settings(cfg)


def test_set_key_writes_env_and_never_returns_the_key(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# keys\nSUPABASE_URL=https://x\nTEST_OPENAI_KEY=old\n")
    r = admin.set_key(env, "TEST_OPENAI_KEY", "sk-secret-123")
    assert r == {"env": "TEST_OPENAI_KEY", "replaced": True, "key_present": True}
    assert env.read_text() == "# keys\nSUPABASE_URL=https://x\nTEST_OPENAI_KEY=sk-secret-123\n"
    assert oct(env.stat().st_mode)[-3:] == "600"
    assert admin.set_key(env, "NEW_KEY", "abc")["replaced"] is False and env.read_text().endswith("NEW_KEY=abc\n")
    for bad in (("lower_case", "x"), ("OK_NAME", ""), ("OK_NAME", "a\nb")):
        with pytest.raises(admin.AdminError):
            admin.set_key(env, *bad)
    monkeypatch.delenv("TEST_OPENAI_KEY", raising=False)
    monkeypatch.delenv("NEW_KEY", raising=False)


def test_check_model_reports_ok_and_failure(cfg):
    # the fake adapter is hidden from `providers()`, so write it straight into the file
    (cfg / "models.toml").write_text((cfg / "models.toml").read_text() + '\n[profiles.fake-1]\nprovider = "fake"\n'
                                     'model_id = "f"\naccepts_images = true\ninput_price = 1.0\noutput_price = 2.0\n')
    fake.Adapter.responder = staticmethod(lambda req: {"answer": "ready", "colour": "red"})
    fake.Adapter.calls = []
    r = admin.check_model(cfg, "fake-1")
    assert r["ok"] and r["image_ok"] and r["cost_usd"] == pytest.approx((1000 * 1 + 100 * 2) / 1e6)
    assert fake.Adapter.calls[0].images[0].data.startswith(b"\x89PNG")
    fake.Adapter.responder = staticmethod(lambda req: RateLimited("slow"))
    assert admin.check_model(cfg, "fake-1")["ok"] is False


# ------------------------------------------------------------------ openai_compatible adapter

def profile(cfg, **over):
    admin.add_model(cfg, "gpt", {**GPT, **over}, replace=True)
    return load_settings(cfg).profile("gpt")


def adapter(cfg, handler, monkeypatch, **over):
    monkeypatch.setenv("TEST_OPENAI_KEY", "sk-test")
    monkeypatch.setattr(oc.Adapter, "transport", httpx.MockTransport(handler))
    return get_provider("openai_compatible", profile(cfg, **over))


REQ = ModelRequest("tag", "instructions", "product text", [Image(b"\xff\xd8\xffjpeg", "image/jpeg", "p/1.jpg")],
                   {"type": "object", "properties": {"a": {"type": "array", "items": {"type": "string"},
                                                           "maxItems": 2}},
                    "required": ["a"], "additionalProperties": False})


def reply(content='{"a": ["x"]}', finish="stop", usage=None, **msg):
    return httpx.Response(200, json={"id": "r1", "model": "some-model-2026", "choices": [
        {"message": {"content": content, **msg}, "finish_reason": finish}],
        "usage": usage or {"prompt_tokens": 1200, "completion_tokens": 50,
                           "prompt_tokens_details": {"cached_tokens": 1000}}})


def test_request_body_and_parsed_reply(cfg, monkeypatch):
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"] = req.headers.get("authorization")
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        return reply()
    a = adapter(cfg, handler, monkeypatch, extra={"temperature": 0})
    r = a.complete(REQ)
    body = seen["body"]
    assert seen["auth"] == "Bearer sk-test" and seen["url"] == "https://api.example.test/v1/chat/completions"
    assert body["model"] == "some-model" and body["temperature"] == 0 and body["max_tokens"] == 16000
    assert body["messages"][0] == {"role": "system", "content": "instructions"}
    user = body["messages"][1]["content"]
    assert user[0]["image_url"]["url"].startswith("data:image/jpeg;base64,") and user[1]["text"] == "product text"
    fmt = body["response_format"]["json_schema"]
    assert fmt["strict"] is True and "maxItems" not in json.dumps(fmt["schema"])
    assert r.parsed == {"a": ["x"]} and (r.input_tokens, r.cached_input_tokens, r.output_tokens) == (200, 1000, 50)
    assert r.served_model == "some-model-2026"


def test_json_object_mode_no_images_and_no_key(cfg, monkeypatch):
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        seen["auth"] = req.headers.get("authorization")
        return reply(content='```json\n{"a": ["y"]}\n```')
    a = adapter(cfg, handler, monkeypatch, json_mode="json_object", accepts_images=False, api_key_env="",
                max_tokens_field="max_completion_tokens")
    assert a.complete(REQ).parsed == {"a": ["y"]}                  # code fences are tolerated
    b = seen["body"]
    assert b["response_format"] == {"type": "json_object"} and "JSON schema" in b["messages"][0]["content"]
    assert len(b["messages"][1]["content"]) == 1 and "max_completion_tokens" in b and seen["auth"] is None


@pytest.mark.parametrize("response, error", [
    (httpx.Response(429, headers={"retry-after": "7"}, text="slow"), RateLimited),
    (httpx.Response(503, text="busy"), RetryableError),
    (httpx.Response(400, text="bad model"), FatalError),
    (reply(finish="length"), SchemaError),
    (reply(content="not json"), SchemaError),
    (reply(content=None, refusal="no"), FatalError),
])
def test_errors_map_to_retry_rules(cfg, monkeypatch, response, error):
    a = adapter(cfg, lambda req: response, monkeypatch)
    with pytest.raises(error) as e:
        a.complete(REQ)
    if error is RateLimited:
        assert e.value.retry_after == 7
    if error is SchemaError:
        assert e.value.result.output_tokens == 50                  # tokens still logged and costed


def test_missing_key_or_base_url_is_a_clear_error(cfg, monkeypatch):
    monkeypatch.delenv("TEST_OPENAI_KEY", raising=False)
    with pytest.raises(FatalError, match="models set-key TEST_OPENAI_KEY"):
        get_provider("openai_compatible", profile(cfg))
