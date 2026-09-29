"""
ContextServe Hybrid Token Gateway Client.
Provides sub-20ms System 1 decisions with local offline heuristic fallback.
"""
from __future__ import annotations

import os
from typing import Any

import httpx


def _local_heuristic_decision(prompt: str) -> str:
    """Deterministic local fallback decision when gateway is offline."""
    lower = prompt.lower().strip()
    
    if any(q in lower for q in ["is relevant", "is this relevant", "relevant?", "should include"]):
        keywords = ["order", "user", "auth", "payment", "service", "handler", "route", "target", "schema"]
        return "true" if any(k in lower for k in keywords) else "false"

    if any(q in lower for q in ["continue", "should continue", "enough context"]):
        return "true" if "stop" not in lower and "done" not in lower else "false"

    if any(e in lower for e in ["401", "403", "unauthorized", "forbidden", "token expired", "auth"]):
        return "authentication_error"
    if any(e in lower for e in ["timeout", "timed out", "econnreset", "connection refused", "504"]):
        return "network_timeout"
    if any(e in lower for e in ["404", "not found", "nosuchkey", "missing"]):
        return "resource_not_found"
    if any(e in lower for e in ["syntax", "typeerror", "referenceerror", "attributeerror", "valueerror"]):
        return "code_defect"

    return "true"


def _local_heuristic_score(prompt: str) -> float:
    """Deterministic local fallback relevance score in [0.0, 1.0]."""
    lower = prompt.lower().strip()
    weights = {
        "query": 0.25,
        "target": 0.25,
        "match": 0.20,
        "relevant": 0.15,
        "caller": 0.10,
        "callee": 0.10,
        "contract": 0.15,
        "impact": 0.15,
    }
    base = 0.50
    matched = sum(w for word, w in weights.items() if word in lower)
    if "irrelevant" in lower or "skip" in lower:
        base -= 0.30
    return round(max(0.01, min(0.99, base + (matched * 0.5))), 4)


class GatewayClient:
    """High-performance client for LiteLLM Gateway & Laya Decision Engine."""

    def __init__(
        self,
        gateway_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 2.0,
        enable_local_fallback: bool = True,
    ):
        self.gateway_url = (
            gateway_url
            or os.environ.get("CONTEXTSERVE_GATEWAY_URL")
            or "http://localhost:4000"
        ).rstrip("/")
        self.api_key = api_key or os.environ.get("CONTEXTSERVE_GATEWAY_KEY") or "sk-cs-default"
        self.timeout_seconds = timeout_seconds
        self.enable_local_fallback = enable_local_fallback

    def check_health(self) -> dict[str, Any]:
        """Check status of LiteLLM Gateway and Laya backend."""
        try:
            with httpx.Client(timeout=1.5) as client:
                resp = client.get(f"{self.gateway_url}/health")
                if resp.status_code == 200:
                    return {"status": "connected", "endpoint": self.gateway_url, "healthy": True}
                return {"status": "degraded", "endpoint": self.gateway_url, "code": resp.status_code}
        except Exception as exc:
            return {"status": "offline", "endpoint": self.gateway_url, "error": str(exc)}

    def fast_decision(self, prompt: str, model: str = "laya-decision") -> str:
        """Executes a fast System 1 decision (< 20ms) via the gateway."""
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                resp = client.post(
                    f"{self.gateway_url}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data["choices"][0]["message"]["content"]
                elif resp.status_code == 402:
                    raise RuntimeError("Gateway 402: Tenant balance depleted or budget ceiling reached")
        except Exception:
            if not self.enable_local_fallback:
                raise

        # Local fallback
        return _local_heuristic_decision(prompt)

    async def fast_decision_async(self, prompt: str, model: str = "laya-decision") -> str:
        """Asynchronous execution for high-concurrency MCP and async pipelines."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.post(
                    f"{self.gateway_url}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data["choices"][0]["message"]["content"]
                elif resp.status_code == 402:
                    raise RuntimeError("Gateway 402: Tenant balance depleted or budget ceiling reached")
        except Exception:
            if not self.enable_local_fallback:
                raise

        return _local_heuristic_decision(prompt)

    def relevance_score(self, prompt: str, model: str = "laya-relevance-scorer") -> float:
        """Continuous relevance scoring in [0.0, 1.0]."""
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                resp = client.post(
                    f"{self.gateway_url}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    text = data["choices"][0]["message"]["content"]
                    return float(text)
        except Exception:
            if not self.enable_local_fallback:
                raise

        return _local_heuristic_score(prompt)


# Default global instances
_default_client = GatewayClient()


def fast_decision(prompt: str) -> str:
    """Global fast decision helper."""
    return _default_client.fast_decision(prompt)


async def fast_decision_async(prompt: str) -> str:
    """Global async fast decision helper."""
    return await _default_client.fast_decision_async(prompt)


def relevance_score(prompt: str) -> float:
    """Global relevance score helper."""
    return _default_client.relevance_score(prompt)
