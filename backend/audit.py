"""Hash-chained audit log, signed certificates and deterministic replay.

* ``AuditLog``: append-only JSONL. Each entry stores the previous entry's hash,
  and its own hash is SHA-256 of the entry plus that previous hash. Editing or
  removing any line breaks every hash after it.
* ``issue_certificate``: a signed record of one report's check: the claims,
  the numbers as parsed, the verdicts, the tolerance policy and the data
  snapshot used.
* ``replay_certificate``: re-runs the checker on a certificate. No LLM is
  involved, so a third party gets the same verdicts from the same inputs.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .check import Policy, check_claim
from .edgar import FactStore
from .models import Claim
from .sanitize import sanitize

GENESIS_HASH = "0" * 64
CERTIFICATE_VERSION = 1


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _portable(obj):
    """Numbers as fixed-format strings, so 391000000000.0 and 391000000000 hash the same."""
    if isinstance(obj, bool) or obj is None or isinstance(obj, str):
        return obj
    if isinstance(obj, (int, float)):
        return format(float(obj), ".15g")
    if isinstance(obj, dict):
        return {key: _portable(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_portable(value) for value in obj]
    return obj


def portable(obj) -> str:
    """Canonical form that survives a JSON round trip through another language.

    A certificate travels to the browser and back. JavaScript writes 391000000000.0
    as 391000000000, which would change a byte-level hash. Certificates are signed
    over this form instead. The audit log never leaves Python and keeps ``canonical``.
    """
    return canonical(_portable(obj))


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def entry_hash(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return sha256_hex(canonical(body) + entry["prev_hash"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class AuditLog:
    """Append-only, hash-chained JSONL file."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path or config.AUDIT_LOG_PATH)
        self._lock = threading.Lock()
        self._count, self._head = self._load_head()

    def _load_head(self) -> tuple[int, str]:
        if not self.path.exists():
            return 0, GENESIS_HASH
        count, head = 0, GENESIS_HASH
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    count += 1
                    try:
                        head = json.loads(line).get("hash", head)
                    except json.JSONDecodeError:
                        pass  # verify_chain reports the damage; appends continue from the last good hash
        return count, head

    def append(self, payload: dict) -> dict:
        with self._lock:
            entry = {"index": self._count, "timestamp": _now(), **payload, "prev_hash": self._head}
            entry["hash"] = entry_hash(entry)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(canonical(entry) + "\n")
            self._count += 1
            self._head = entry["hash"]
            return entry

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        out: list[dict] = []
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        out.append({"corrupt": True, "raw": line.strip()[:200]})
        return out

    def tail(self, limit: int = 50) -> list[dict]:
        return self.entries()[-limit:]

    @property
    def head(self) -> str:
        return self._head

    def __len__(self) -> int:
        return self._count

    def verify_chain(self, entries: list[dict] | None = None) -> dict:
        """Recompute every hash. Reports the first entry that does not hold."""
        entries = self.entries() if entries is None else entries
        previous = GENESIS_HASH
        for position, entry in enumerate(entries):
            problem = None
            if entry.get("corrupt"):
                problem = "entry is not valid JSON"
            elif entry.get("index") != position:
                problem = "entry is out of sequence (an entry was removed or inserted)"
            elif entry.get("prev_hash") != previous:
                problem = "previous-hash link does not match"
            elif entry.get("hash") != entry_hash(entry):
                problem = "entry content does not match its hash (it was edited)"
            if problem:
                return {"ok": False, "entries": len(entries), "broken_at": position, "reason": problem, "head": previous}
            previous = entry["hash"]
        return {"ok": True, "entries": len(entries), "broken_at": None, "reason": None, "head": previous}

    def simulate_tamper(self) -> dict:
        """Verify an in-memory copy with one verdict flipped. The file is not touched."""
        entries = self.entries()
        verdict_rows = [i for i, e in enumerate(entries) if e.get("type") == "verdict"]
        target = verdict_rows[len(verdict_rows) // 2] if verdict_rows else None
        if target is None:
            return {"ok": True, "entries": len(entries), "broken_at": None, "reason": "nothing to tamper with", "head": self.head}
        tampered = [dict(e) for e in entries]
        tampered[target]["status"] = "VERIFIED" if tampered[target].get("status") != "VERIFIED" else "WRONG_VALUE"
        result = self.verify_chain(tampered)
        result["simulated"] = True
        result["tampered_index"] = target
        return result


# --------------------------------------------------------------------------
# Signing
# --------------------------------------------------------------------------

def load_signing_key() -> bytes:
    """Key for certificate signatures: from the environment, else a local file."""
    from_env = os.environ.get("PROOFLINE_SIGNING_KEY")
    if from_env:
        return from_env.encode("utf-8")
    path = config.SIGNING_KEY_PATH
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32), encoding="utf-8")
        path.chmod(0o600)
    return path.read_text(encoding="utf-8").strip().encode("utf-8")


def sign(body: dict, key: bytes | None = None) -> str:
    return hmac.new(key or load_signing_key(), portable(body).encode("utf-8"), hashlib.sha256).hexdigest()


# --------------------------------------------------------------------------
# Certificates
# --------------------------------------------------------------------------

def issue_certificate(*, status: str, report: dict, checks: list[dict], unchecked: list[dict],
                      counts: dict, policy: Policy, extractor: dict, engine: str,
                      store: FactStore, log: AuditLog) -> dict:
    """Write the verdicts to the audit log and return a signed certificate."""
    first_index = len(log)
    for check in checks:
        log.append({
            "type": "verdict",
            "report_sha256": report["sha256"],
            "claim": check["claim"],
            "value": check["value"],
            "status": check["status"],
            "expected": check["expected"],
            "accessions": check["accessions"],
        })
    for item in unchecked:
        log.append({
            "type": "unchecked",
            "report_sha256": report["sha256"],
            "token": item["token"],
            "value": item["value"],
            "status": "UNCHECKED",
        })
    body = {
        "version": CERTIFICATE_VERSION,
        "engine": engine,
        "issued_at": _now(),
        "status": status,
        "report": report,
        "data_snapshot": store.snapshot_id(),
        "policy": policy.to_dict(),
        "extractor": {k: extractor.get(k) for k in ("extractor", "model", "provider")},
        "counts": counts,
        "checks": checks,
        "unchecked": unchecked,
        "log_range": {"first_index": first_index, "last_index": len(log) - 1},
    }
    certificate_id = sha256_hex(portable(body))
    anchor = log.append({
        "type": "certificate",
        "report_sha256": report["sha256"],
        "certificate_id": certificate_id,
        "status": status,
        "counts": counts,
    })
    return {
        "certificate_id": certificate_id,
        "body": body,
        "signature": {"algorithm": "HMAC-SHA256", "value": sign(body)},
        "anchor": {"index": anchor["index"], "hash": anchor["hash"]},
    }


def overall_status(statuses: list[str]) -> str:
    red = {"WRONG_VALUE", "WRONG_PERIOD", "SCALE_ERROR"}
    amber = {"UNSUPPORTED", "UNCHECKED"}
    if any(s in red for s in statuses):
        return "REJECTED"
    if any(s in amber for s in statuses):
        return "INCOMPLETE"
    return "CERTIFIED" if statuses else "EMPTY"


def replay_certificate(certificate: dict, store: FactStore, log: AuditLog, text: str | None = None) -> dict:
    """Re-run a certificate's checks in code and confirm nothing differs."""
    started = time.perf_counter()
    body = certificate.get("body") or {}
    signature = (certificate.get("signature") or {}).get("value", "")
    signature_valid = hmac.compare_digest(signature, sign(body)) if body else False
    id_valid = certificate.get("certificate_id") == sha256_hex(portable(body)) if body else False

    anchor = certificate.get("anchor") or {}
    entries = log.entries()
    chain = log.verify_chain(entries)
    index = anchor.get("index")
    anchored = (
        chain["ok"] and isinstance(index, int) and 0 <= index < len(entries)
        and entries[index].get("hash") == anchor.get("hash")
        and entries[index].get("certificate_id") == certificate.get("certificate_id")
    )

    snapshot_match = body.get("data_snapshot") == store.snapshot_id()
    try:
        policy = Policy(**body.get("policy", {}))
    except TypeError:
        policy = Policy()

    mismatches: list[dict] = []
    statuses: list[str] = []
    for position, check in enumerate(body.get("checks", [])):
        try:
            claim = Claim.model_validate(check["claim"])
            verdict = check_claim(claim, store, value=check["value"], quantum=check.get("quantum"),
                                  token_unit=check.get("token_unit"), policy=policy)
        except Exception as exc:  # a malformed certificate is a failed replay, not a crash
            mismatches.append({"index": position, "error": f"could not replay: {type(exc).__name__}"})
            continue
        statuses.append(verdict.status.value)
        same_expected = (
            (verdict.expected is None and check.get("expected") is None)
            or (verdict.expected is not None and check.get("expected") is not None
                and abs(verdict.expected - check["expected"]) <= 1e-9 * max(1.0, abs(verdict.expected)))
        )
        if verdict.status.value != check.get("status") or not same_expected:
            mismatches.append({
                "index": position, "span": claim.span,
                "certificate": {"status": check.get("status"), "expected": check.get("expected")},
                "replayed": {"status": verdict.status.value, "expected": verdict.expected},
            })
    statuses += ["UNCHECKED"] * len(body.get("unchecked", []))
    status_match = overall_status(statuses) == body.get("status")

    text_match = None
    if text is not None:
        clean = sanitize(text).text
        text_match = sha256_hex(clean) == (body.get("report") or {}).get("sha256")
        if text_match:
            for check in body.get("checks", []):
                token = check.get("token")
                if token and clean[token["start"]:token["end"]] != token["text"]:
                    text_match = False
                    break

    checks_ok = not mismatches and status_match
    valid = bool(signature_valid and id_valid and anchored and snapshot_match and checks_ok and text_match is not False)
    return {
        "valid": valid,
        "signature_valid": signature_valid,
        "certificate_id_valid": id_valid,
        "anchored_in_log": bool(anchored),
        "chain_ok": chain["ok"],
        "data_snapshot_match": snapshot_match,
        "text_match": text_match,
        "verdicts_replayed": len(body.get("checks", [])),
        "verdicts_match": checks_ok,
        "mismatches": mismatches,
        "llm_calls": 0,
        "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
    }
