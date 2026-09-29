"""
Tests for Laya Decision Engine FastAPI Service (services/laya-service/main.py).
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Add services/laya-service to path
SERVICE_DIR = Path(__file__).resolve().parents[1] / "services" / "laya-service"
sys.path.insert(0, str(SERVICE_DIR))

try:
    from main import app, load_laya
except ImportError:
    pytest.skip("services/laya-service dependencies not available", allow_module_level=True)


@pytest.fixture(autouse=True)
def init_engine():
    load_laya()


@pytest.fixture
def client():
    return TestClient(app)


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["engine"] == "laya"
    assert data["ready"] is True


def test_list_models(client):
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    model_ids = [m["id"] for m in data["data"]]
    assert "laya-decision" in model_ids
    assert "laya-relevance-scorer" in model_ids


def test_chat_completions_decision(client):
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "laya-decision",
            "messages": [
                {"role": "user", "content": "Is the order payment service relevant to payment processing?"}
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "chat.completion"
    assert data["model"] == "laya-decision"
    assert len(data["choices"]) == 1
    content = data["choices"][0]["message"]["content"]
    assert content in ("true", "false") or len(content) > 0
    assert "usage" in data
    assert data["usage"]["prompt_tokens"] > 0
    assert data["usage"]["completion_tokens"] > 0
    assert data["usage"]["total_tokens"] == data["usage"]["prompt_tokens"] + data["usage"]["completion_tokens"]
    assert "latency_ms" in data
    assert data["latency_ms"] >= 0


def test_chat_completions_scorer(client):
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "laya-relevance-scorer",
            "messages": [
                {"role": "user", "content": "Score relevance for order schema query target match"}
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == "laya-relevance-scorer"
    content = data["choices"][0]["message"]["content"]
    score = float(content)
    assert 0.0 <= score <= 1.0


def test_chat_completions_empty_messages(client):
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "laya-decision",
            "messages": [],
        },
    )
    assert resp.status_code == 400


def test_direct_rpc_endpoints(client):
    resp_dec = client.post("/v1/decisions", json={"prompt": "Is auth token expired?"})
    assert resp_dec.status_code == 200
    assert "decision" in resp_dec.json()
    assert resp_dec.json()["engine"] == "laya-system1"

    resp_score = client.post("/v1/score", json={"query": "order", "candidate": "order_service"})
    assert resp_score.status_code == 200
    assert "score" in resp_score.json()
    assert isinstance(resp_score.json()["score"], float)
