"""API tests through FastAPI's test client. The extractor is forced to the rule-based one."""
import json

import pytest
from fastapi.testclient import TestClient

from backend import app as app_module
from backend import llm
from backend.audit import AuditLog

CLEAN = "Apple reported revenue of $391.0 billion in fiscal 2024. Net income was $93.7 billion."


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROOFLINE_SIGNING_KEY", "test-key")
    monkeypatch.setenv("PROOFLINE_LLM", "none")
    monkeypatch.setattr(app_module, "_log", AuditLog(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(app_module, "_limiter", app_module.RateLimiter())
    return TestClient(app_module.app)


def test_health_and_meta(client):
    assert client.get("/health").json()["ok"] is True
    meta = client.get("/api/meta").json()
    assert {c["ticker"] for c in meta["companies"]} == {"AAPL", "AMZN", "MSFT", "NVDA", "TSLA"}
    assert meta["llm"]["provider"] == "none" and len(meta["data_snapshot"]) == 64
    assert {m["key"] for m in meta["metrics"]} >= {"revenue", "eps_diluted"}


def test_verify_returns_the_documented_shape(client):
    body = client.post("/verify", json={"text": CLEAN}).json()
    assert {"claims", "verdicts", "unchecked", "summary"} <= set(body)
    assert body["summary"]["status"] == "CERTIFIED"
    assert body["extractor"]["extractor"] == "rules"
    assert client.post("/api/verify", json={"text": CLEAN}).status_code == 200


def test_verify_coverage_toggle_and_defences(client):
    text = CLEAN + " Units sold were 232 million."
    assert len(client.post("/verify", json={"text": text}).json()["unchecked"]) == 1
    off = client.post("/verify", json={"text": text, "coverage": False}).json()
    assert off["unchecked"] == [] and off["certificate"] is None
    custom = client.post("/verify", json={"text": text, "defences": {"coverage": False, "sanitize": False,
                                                                     "bind": False, "anti_evasion": False}}).json()
    assert custom["summary"]["status"] == "UNCERTIFIED"


@pytest.mark.parametrize("payload", [{}, {"text": ""}, {"text": "x" * 60_001}, {"text": "ok", "extractor": "magic"}])
def test_verify_rejects_bad_input(client, payload):
    assert client.post("/verify", json=payload).status_code == 422


def test_llm_endpoints_report_missing_backend(client):
    assert client.post("/naive", json={"text": CLEAN}).status_code == 503
    assert client.post("/ask", json={"question": "What was Apple revenue in fiscal 2024?"}).status_code == 503
    assert client.post("/verify", json={"text": CLEAN, "extractor": "llm"}).status_code == 503


def test_llm_failure_maps_to_bad_gateway(client, monkeypatch):
    def failing(text):
        raise llm.LLMError("upstream broke")
    monkeypatch.setattr(app_module.naive, "naive_check", failing)
    response = client.post("/naive", json={"text": CLEAN})
    assert response.status_code == 502 and response.json()["detail"] == "upstream broke"


def test_ask_runs_agent_then_guard(client, monkeypatch):
    monkeypatch.setattr(app_module.guard, "agent_answer", lambda question: {
        "answer": "Apple revenue was $500 billion in fiscal 2024.", "model": "m", "provider": "test",
        "cached": False, "latency_ms": 1.0})
    body = client.post("/ask", json={"question": "What was Apple revenue in fiscal 2024?"}).json()
    assert body["action"] == "corrected"
    assert body["released_text"] == "Apple revenue was $391.0 billion in fiscal 2024."
    assert body["verification"]["summary"]["status"] == "REJECTED"


def test_guard_endpoint(client):
    body = client.post("/guard", json={"text": CLEAN.replace("$93.7 billion", "$99.9 billion")}).json()
    assert body["corrected"] == 1 and "$93.7 billion" in body["released_text"]


def test_audit_and_replay_round_trip(client):
    certificate = client.post("/verify", json={"text": CLEAN}).json()["certificate"]
    log = client.get("/audit?limit=5").json()
    assert log["total"] == 3 and log["entries"][-1]["type"] == "certificate"
    assert client.post("/audit/verify").json()["ok"] is True
    assert client.post("/audit/verify", json={"simulate_tamper": True}).json()["ok"] is False
    replay = client.post("/replay", json={"certificate": certificate, "text": CLEAN}).json()
    assert replay["valid"] is True and replay["llm_calls"] == 0


def test_facts_table(client):
    body = client.get("/facts/aapl").json()
    revenue = next(r for r in body["rows"] if r["metric"] == "revenue")
    assert revenue["values"]["2024"]["value"] == 391_035_000_000
    assert client.get("/facts/ZZZZ").status_code == 404


def test_samples_and_results(client, tmp_path, monkeypatch):
    samples_dir = tmp_path / "samples"
    samples_dir.mkdir()
    (samples_dir / "a.txt").write_text("hello")
    (samples_dir / "index.json").write_text(json.dumps([
        {"id": "a", "title": "A", "file": "a.txt"}, {"id": "b", "title": "B", "file": "../secret.txt"},
        {"id": "c", "title": "C", "file": "missing.txt"}]))
    monkeypatch.setattr(app_module.config, "SAMPLES_DIR", samples_dir)
    listed = client.get("/samples").json()["samples"]
    assert [s["id"] for s in listed] == ["a"] and listed[0]["text"] == "hello"
    monkeypatch.setattr(app_module.config, "SAMPLES_DIR", tmp_path / "nowhere")
    assert client.get("/samples").json() == {"samples": []}

    result_file = tmp_path / "results.json"
    monkeypatch.setitem(app_module.RESULT_FILES, "redteam", result_file)
    assert client.get("/results/redteam").json() == {"available": False}
    result_file.write_text(json.dumps({"rows": [1]}))
    assert client.get("/results/redteam").json() == {"available": True, "rows": [1]}
    assert client.get("/results/nope").status_code == 404


def test_rate_limit(client, monkeypatch):
    monkeypatch.setattr(app_module, "RATE_LIMIT_DEFAULT", 3)
    codes = [client.get("/health").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]


def test_security_headers(client):
    assert client.get("/health").headers["x-content-type-options"] == "nosniff"
