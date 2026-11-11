# ProofLine

A fact-checker for AI-written financial analysis. It pulls every number out of a report and checks each one
against the company's SEC filings. Every number comes back verified, wrong (with the filed figure) or not
checkable, with a link to the filing.

> We don't use an AI to check an AI.

The LLM only reads the text and structures the claims. All checking is plain Python, so a verdict cannot be
hallucinated and every verdict can be replayed.

IEEE hackathon, LSEG track (Finance, Data and Compliance).

## Quick start

```bash
# 1. Python backend (needs uv: https://docs.astral.sh/uv/)
uv sync
uv run pytest                       # 207 tests, no network, no LLM

# 2. Front end
cd frontend && npm install && npm run build && cd ..

# 3. Run. The API serves the built UI on the same port.
uv run uvicorn backend.app:app --port 8000
# open http://localhost:8000
```

For UI development run `npm run dev` in `frontend/` (port 5173, proxies `/api` to port 8000).

### The LLM call

Copy `.env.example` to `.env`. ProofLine picks a backend in this order:

1. `ANTHROPIC_API_KEY` set: the Anthropic SDK.
2. No key, but the `claude` CLI is installed and logged in: the CLI in print mode.
3. Neither: the offline rule-based extractor. The demo still runs, with less flexible reading.

The model is `PROOFLINE_MODEL` (default `claude-opus-5-5`). LLM responses are cached in `llm_cache/` by request
hash, so the samples and all evaluation runs replay offline.

### Data

`data/*.json` holds annual 10-K figures for AAPL, MSFT, NVDA, TSLA and AMZN, fetched once from SEC EDGAR.
`data/filings/` holds the financial statements of each 10-K as text (used by the leaderboard). To refresh,
set `SEC_USER_AGENT` in `.env` to your name and email, then:

```bash
uv run python scripts/fetch_data.py            # company facts
uv run python scripts/fetch_filings.py         # 10-K statement text
```

## How it works

```
Report text
   |
   v
[sanitise] strip hidden text (HTML comments, zero-width and tag characters), record what was removed
   |
   v
[1] Claim extractor (one LLM call, JSON schema only, no verdict field)
   |            \
   v             v
[2] Checker   [3] Coverage check: every numeric token must be bound to exactly one claim
 (Python)        (regex + number parser, no AI)
   |             /
   v            v
[4] Verdicts + hash-chained audit log + signed certificate  -->  [5] Web UI
          ^
[0] EDGAR fact store (local JSON)
```

| Module | Role | LLM? |
|---|---|---|
| `backend/edgar.py` | Fetcher and offline fact store. `get_fact("AAPL", "revenue", 2024)` | no |
| `backend/numparse.py` | Finds and normalises every number, including spelled-out and split-digit ones | no |
| `backend/check.py` | The checker: tolerance rules, five verdicts, calculation text, filing links | no |
| `backend/coverage.py` | Binds each claim to one number in the text; anything unbound is UNCHECKED | no |
| `backend/sanitize.py` | Hidden-text stripping and instruction-like text detection | no |
| `backend/extract.py` | The one LLM call: report text in, structured claims out | yes |
| `backend/heuristic.py` | Offline rule-based extractor (fallback) | no |
| `backend/audit.py` | Hash-chained JSONL log, HMAC-signed certificates, deterministic replay | no |
| `backend/guard.py` | Guardrail mode: correct or flag figures before an agent's answer is released | answer only |
| `backend/pipeline.py`, `backend/app.py` | Orchestration and the HTTP API | |

A test (`tests/test_check.py`) fails the build if a checking module ever imports an LLM client.

### Verdicts

`VERIFIED`, `WRONG_VALUE` (with the filed figure), `WRONG_PERIOD` (the value belongs to another year),
`SCALE_ERROR` (off by 1,000 or more), `UNSUPPORTED` (no source data), `UNCHECKED` (a number no claim covers).

Claim kinds: amount, growth rate, compound growth rate, change in dollars, margin, ratio (also across companies).

Tolerance: a figure passes if the filed value rounds to it at the precision the text states ("$391bn" accepts
390.5 to 391.5bn). Percentages pass within 0.5 percentage points. The policy is stored in every certificate.

### Why prompt injection does not work

1. The extractor's schema has no verdict field. It cannot mark anything verified.
2. Each claim's span must appear verbatim in the report, and its value is re-parsed from the text by code.
   The model's own `value` is ignored if it disagrees.
3. The coverage check flags every number that no claim accounts for, so a skipped number is visible.
4. Hidden text is stripped before the model sees it, and reported.
5. Spelled-out numbers and digits split by spaces are parsed too.

### API

| Endpoint | Purpose |
|---|---|
| `POST /verify {text, coverage?, defences?}` | `{claims, verdicts, unchecked, summary, certificate, ...}` |
| `POST /naive {text}` | Baseline: an LLM asked "is this accurate?" with no source data |
| `POST /ask {question}` | An agent answers from memory, then ProofLine corrects or flags it |
| `POST /guard {text}` | Guardrail on any text |
| `POST /replay {certificate, text?}` | Re-run a certificate's checks in code; 0 LLM calls |
| `GET /audit`, `POST /audit/verify` | Audit log and chain verification |
| `GET /facts/{ticker}`, `GET /results/{redteam,eval,leaderboard}`, `GET /samples`, `GET /meta` | Data for the UI |

All routes are also served under `/api`.

## Results

Numbers below are copied from the result files in this repo. Re-run the scripts to regenerate them.

```bash
uv run python redteam/run_attacks.py     # redteam/results.json
uv run python eval/run_eval.py           # eval/results.json, dataset.json, labels.json
uv run python eval/leaderboard.py        # eval/leaderboard.json, samples/real_failures/
```

RESULTS_PLACEHOLDER

## Demo script (about 4 minutes)

1. **Leaderboard tab.** "We asked 60 questions to three models. This share of answers had a wrong number."
   Open one real failure in the checker.
2. **Checker tab, clean report.** Verify. All green, certificate issued. Press Replay: every verdict recomputed
   in about a millisecond with 0 LLM calls. A clean report with a certificate is the product.
3. **Report with errors.** Verify. Click the red numbers: filed figure, calculation, link to the 10-K.
4. **Poisoned report.** Ask the naive AI checker first. Then Verify: hidden text removed, instructions
   underlined, the wrong figures still red.
5. **Security tab.** The table for all 100 poisoned reports.
6. **Guardrail tab.** A judge asks their own question. Left: what the agent said. Right: what the reader gets.
7. **Audit log tab.** Verify chain, then Simulate tampering.
8. **Evaluation tab.** Catch rate, false alarms, speed. Limits: US 10-K filings, numeric claims, five companies.

## Limits

- US annual filings only (10-K). No quarterly data yet.
- Numeric claims only. Text claims ("management flagged supply chain risk") are not checked.
- Five companies are cached. Others return "no source".
- Segment figures, share prices and forecasts are reported as "no source", not checked.
- A number written in a form the parser does not recognise would not be flagged. The parser covers digits,
  scale words, spelled-out numbers and split digits.
- Certificates are signed with HMAC-SHA256 and a local key. Production would use an asymmetric key in an HSM.

## Repo map

```
backend/      checker, extractor, API
frontend/     React + Vite UI
data/         cached EDGAR facts and 10-K statement text
samples/      demo reports, attack samples, real model failures
redteam/      prompt-injection harness and results
eval/         evaluation, leaderboard, datasets, results
tests/        pytest suite
llm_cache/    cached LLM responses (makes every run reproducible offline)
scripts/      one-off data fetchers
```
