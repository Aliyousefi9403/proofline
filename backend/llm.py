"""LLM access for the extractor and the baselines.

One function, ``complete``, with two interchangeable backends:

* ``anthropic``  : the official SDK (needs ANTHROPIC_API_KEY or another SDK credential)
* ``claude-cli`` : the local ``claude`` command in print mode, for machines that are
                   logged in to Claude Code but have no API key

Responses are cached on disk by request hash, so evaluation runs are repeatable
and the demo samples work offline. The checker never imports this module.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import config

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_MAX_TOKENS = 16000
CLI_TIMEOUT_SECONDS = 300
# Models whose requests opt in to server-side refusal fallbacks.
FALLBACK_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2", "claude-fable-5-1", "claude-opus-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
_CLI_ENV_DROP = (
    "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_PID", "CLAUDE_EFFORT",
    "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_SESSION_ATTENDED",
)


class LLMUnavailable(RuntimeError):
    """No LLM backend is configured on this machine."""


class LLMError(RuntimeError):
    """The backend was reached but the call failed."""


@dataclass(frozen=True)
class LLMResult:
    data: Any            # parsed JSON when a schema was given, else the text
    model: str
    provider: str
    cached: bool
    latency_ms: float


def default_model() -> str:
    return os.environ.get("PROOFLINE_MODEL") or DEFAULT_MODEL


def provider() -> str:
    """Which backend will be used: 'anthropic', 'claude-cli' or 'none'."""
    choice = (os.environ.get("PROOFLINE_LLM") or "auto").lower()
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    has_cli = shutil.which("claude") is not None
    if choice == "anthropic":
        return "anthropic"
    if choice == "claude-cli":
        return "claude-cli" if has_cli else "none"
    if choice == "none":
        return "none"
    if has_key:
        return "anthropic"
    return "claude-cli" if has_cli else "none"


def available() -> bool:
    return provider() != "none"


# ---- cache -----------------------------------------------------------------

def _cache_key(model: str, system: str, user: str, schema: dict | None, effort: str) -> str:
    payload = json.dumps([model, system, user, schema, effort], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_path(key: str) -> Path:
    return config.LLM_CACHE_DIR / key[:2] / f"{key}.json"


def cache_get(key: str) -> dict | None:
    path = _cache_path(key)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def cache_put(key: str, entry: dict) -> None:
    path = _cache_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ---- backends --------------------------------------------------------------

@lru_cache(maxsize=1)
def _anthropic_client():
    import anthropic

    return anthropic.Anthropic()


def _call_anthropic(system: str, user: str, model: str, schema: dict | None, effort: str, max_tokens: int) -> Any:
    import anthropic

    client = _anthropic_client()
    output_config: dict[str, Any] = {"effort": effort}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    request = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config=output_config,
    )
    try:
        if model in FALLBACK_MODELS:
            try:
                response = client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **request)
            except anthropic.BadRequestError:
                # Fallbacks are not available everywhere; the plain request still is.
                response = client.messages.create(**request)
        else:
            response = client.messages.create(**request)
    except anthropic.AuthenticationError as exc:
        raise LLMUnavailable("Anthropic API key was rejected.") from exc
    except anthropic.RateLimitError as exc:
        raise LLMError("Anthropic API rate limit reached. Try again shortly.") from exc
    except anthropic.APIStatusError as exc:
        raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMError("Could not reach the Anthropic API.") from exc

    if response.stop_reason == "refusal":
        raise LLMError("The model declined this request.")
    if response.stop_reason == "max_tokens":
        raise LLMError("The model ran out of output tokens.")
    text = next((block.text for block in response.content if block.type == "text"), None)
    if text is None:
        raise LLMError("The model returned no text.")
    if schema is None:
        return text
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError("The model returned invalid JSON.") from exc


def _call_cli(system: str, user: str, model: str, schema: dict | None, effort: str) -> Any:
    binary = shutil.which("claude")
    if binary is None:
        raise LLMUnavailable("The claude CLI is not installed.")
    command = [
        binary, "-p", "--model", model, "--system-prompt", system, "--tools", "",
        "--strict-mcp-config", "--disable-slash-commands", "--setting-sources", "",
        "--no-session-persistence", "--output-format", "json", "--effort", effort,
    ]
    if schema is not None:
        command += ["--json-schema", json.dumps(schema)]
    env = {k: v for k, v in os.environ.items() if k not in _CLI_ENV_DROP}
    with tempfile.TemporaryDirectory(prefix="proofline-llm-") as workdir:
        try:
            completed = subprocess.run(
                command, input=user, capture_output=True, text=True, env=env, cwd=workdir,
                timeout=CLI_TIMEOUT_SECONDS, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise LLMError("The claude CLI timed out.") from exc
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        detail = (completed.stderr or completed.stdout or "").strip()[:300]
        raise LLMError(f"The claude CLI returned no JSON. {detail}") from exc
    if payload.get("is_error"):
        raise LLMError(f"The claude CLI reported an error: {str(payload.get('result'))[:300]}")
    if schema is None:
        return payload.get("result") or ""
    structured = payload.get("structured_output")
    if structured is None:
        try:
            structured = json.loads(payload.get("result") or "")
        except json.JSONDecodeError as exc:
            raise LLMError("The claude CLI returned no structured output.") from exc
    return structured


# ---- public API ------------------------------------------------------------

def complete(
    *,
    system: str,
    user: str,
    schema: dict | None = None,
    model: str | None = None,
    effort: str = "low",
    max_tokens: int = DEFAULT_MAX_TOKENS,
    use_cache: bool = True,
    retries: int = 2,
) -> LLMResult:
    """Run one prompt. Returns parsed JSON when ``schema`` is given, else text."""
    model = model or default_model()
    key = _cache_key(model, system, user, schema, effort)
    if use_cache:
        hit = cache_get(key)
        if hit is not None:
            return LLMResult(hit["data"], hit.get("model", model), hit.get("provider", "cache"), True, 0.0)
    backend = provider()
    if backend == "none":
        raise LLMUnavailable(
            "No LLM backend configured. Set ANTHROPIC_API_KEY in .env, or install the claude CLI."
        )
    started = time.perf_counter()
    last_error: LLMError | None = None
    for attempt in range(retries + 1):
        try:
            if backend == "anthropic":
                data = _call_anthropic(system, user, model, schema, effort, max_tokens)
            else:
                data = _call_cli(system, user, model, schema, effort)
            break
        except LLMError as exc:
            last_error = exc
            if attempt == retries:
                raise
            time.sleep(1.5 * (attempt + 1))
    else:  # pragma: no cover - loop always breaks or raises
        raise last_error or LLMError("LLM call failed.")
    latency_ms = (time.perf_counter() - started) * 1000.0
    if use_cache:
        cache_put(key, {"data": data, "model": model, "provider": backend,
                        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    return LLMResult(data, model, backend, False, latency_ms)
