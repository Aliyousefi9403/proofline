"""Fetch SEC company facts once and cache them under data/.

Usage:  uv run python scripts/fetch_data.py [TICKER ...]
        uv run python scripts/fetch_data.py --from-raw   (rebuild from data/raw/*.json)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config, edgar  # noqa: E402


def main(argv: list[str]) -> int:
    config.load_dotenv()
    if argv and argv[0] == "--from-raw":
        raw_dir = config.DATA_DIR / "raw"
        for path in sorted(raw_dir.glob("*.json")):
            if not path.stem.isupper():
                continue
            slim = edgar.slim_company_facts(json.loads(path.read_text()), path.stem)
            out = config.DATA_DIR / f"{path.stem}.json"
            out.write_text(json.dumps(slim, separators=(",", ":")), encoding="utf-8")
            print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
        return 0
    tickers = [t.upper() for t in argv] or list(config.DEFAULT_TICKERS)
    for path in edgar.build_cache(tickers):
        print(f"wrote {path} ({path.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
