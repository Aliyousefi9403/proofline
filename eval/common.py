"""Shared helpers for the evaluation, leaderboard and red-team scripts."""
from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402
from backend.edgar import FactStore  # noqa: E402
from backend.llm import LLMRateLimited  # noqa: E402
from backend.models import AMBER, RED  # noqa: E402

config.load_dotenv()

RED_STATUSES = {s.value for s in RED}
AMBER_STATUSES = {s.value for s in AMBER}
COMPANY_NAMES = {"AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA", "TSLA": "Tesla", "AMZN": "Amazon"}
DEFAULT_WORKERS = 6


def store() -> FactStore:
    return FactStore()


def run_parallel(fn, items: list, *, workers: int = DEFAULT_WORKERS, label: str = "tasks") -> list:
    """Run ``fn`` over ``items`` in threads, keeping order. Failures are returned as exceptions."""
    results: list = [None] * len(items)
    started = time.time()
    limit_hit = threading.Event()

    def guarded(item):
        if limit_hit.is_set():
            raise LLMRateLimited("skipped: the usage limit was reached earlier in this run")
        try:
            return fn(item)
        except LLMRateLimited:
            if not limit_hit.is_set():
                limit_hit.set()
                print("  usage limit reached: skipping the remaining calls. Re-run later; finished calls are cached.", flush=True)
            raise

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(guarded, item): index for index, item in enumerate(items)}
        for done, future in enumerate(as_completed(futures), start=1):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:  # recorded, reported by the caller
                results[index] = exc
            if done % 10 == 0 or done == len(items):
                print(f"  {label}: {done}/{len(items)} ({time.time() - started:.0f}s)", flush=True)
    return results


def overlapping(result: dict, start: int, end: int) -> tuple[list[dict], list[dict]]:
    """Verdicts and unchecked tokens that cover the character range."""
    verdicts = [
        v for v in result["verdicts"]
        if v.get("token") and v["token"]["start"] < end and start < v["token"]["end"]
    ]
    unchecked = [u for u in result["unchecked"] if u["token"]["start"] < end and start < u["token"]["end"]]
    return verdicts, unchecked


def rate(count: int, total: int) -> float:
    return round(count / total, 4) if total else 0.0


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
