"""Paths, environment loading and tunable constants.

Nothing here talks to the network or to an LLM, so every module may import it.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("PROOFLINE_DATA_DIR", ROOT / "data"))
FILINGS_DIR = DATA_DIR / "filings"
SAMPLES_DIR = ROOT / "samples"
STATE_DIR = Path(os.environ.get("PROOFLINE_STATE_DIR", ROOT / ".proofline"))
AUDIT_LOG_PATH = STATE_DIR / "audit.jsonl"
SIGNING_KEY_PATH = STATE_DIR / "signing.key"
LLM_CACHE_DIR = Path(os.environ.get("PROOFLINE_LLM_CACHE", ROOT / "llm_cache"))

DEFAULT_TICKERS = ("AAPL", "MSFT", "NVDA", "TSLA", "AMZN")

# The SEC asks automated clients to identify themselves with a name and an email.
# Set SEC_USER_AGENT in .env to your own contact details before fetching.
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "research-client admin@example.org")
SEC_MIN_INTERVAL_SECONDS = 0.15  # stays well under the 10 requests/second limit

MAX_REPORT_CHARS = 60_000


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env reader (KEY=VALUE lines). Existing environment wins."""
    env_path = path or ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
