#!/usr/bin/env python3
"""
ReelMetrics — automated week-wise box-office updater.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data.json"

IST = ZoneInfo("Asia/Kolkata")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT_SECONDS = 25
MAX_HTML_BYTES = 8_000_000

RELEASE_DATE = "2026-11-05"

# Target URLs for Ramayana box office reports
TARGET_URLS = [
    "https://sacnilk.com",
    "https://sacnilk.com",
    "https://sacnilk.com"
]


def now_ist() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def fetch_page(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return response.read(MAX_HTML_BYTES).decode("utf-8", errors="replace")


class VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def parse_box_office_text(raw_html: str) -> dict[str, Any]:
    parser = VisibleTextParser()
    parser.feed(raw_html)
    text = " ".join(parser.parts)

    result: dict[str, Any] = {"weeks": {}}

    # Extract Worldwide Total Gross
    ww_match = re.search(
        r"(?:Worldwide|WW)\s*(?:Total|Gross|Collection)?[:\-\s₹]*([0-9,]+(?:\.[0-9]+)?)\s*(?:Cr|Crore)",
        text,
        re.IGNORECASE,
    )
    if ww_match:
        val = float(ww_match.group(1).replace(",", ""))
        if val > 50:
            result["worldwideGrossCr"] = val

    # Extract India Gross / Net
    ind_match = re.search(
        r"India\s*(?:Gross|Net|Collection)?[:\-\s₹]*([0-9,]+(?:\.[0-9]+)?)\s*(?:Cr|Crore)",
        text,
        re.IGNORECASE,
    )
    if ind_match:
        val = float(ind_match.group(1).replace(",", ""))
        if val > 40:
            result["indiaGrossCr"] = val

    # Extract Overseas Gross
    ov_match = re.search(
        r"Overseas\s*(?:Gross|Collection)?[:\-\s₹]*([0-9,]+(?:\.[0-9]+)?)\s*(?:Cr|Crore)",
        text,
        re.IGNORECASE,
    )
    if ov_match:
        val = float(ov_match.group(1).replace(",", ""))
        if val > 10:
            result["overseasGrossCr"] = val

    # Extract Week 1, Week 2, Week 3, Week 4 collections
    for wk in range(1, 5):
        wk_match = re.search(
            rf"Week\s*{wk}\s*(?:Collection|Total|Gross|Net)?[:\-\s₹]*([0-9,]+(?:\.[0-9]+)?)\s*(?:Cr|Crore)",
            text,
            re.IGNORECASE,
        )
        if wk_match:
            val = float(wk_match.group(1).replace(",", ""))
            if val > 1:
                result["weeks"][f"week{wk}"] = val

    return result


def main() -> int:
    data = load_json(DATA_FILE, {})
    fetched_at = now_ist()

    # Preserve existing weeklyCollections structure
    weekly = data.setdefault(
        "weeklyCollections",
        {
            "week1": {"label": "Week 1 (Days 1–7)", "worldwideCr": None, "indiaCr": None, "overseasCr": None},
            "week2": {"label": "Week 2 (Days 8–14)", "worldwideCr": None, "indiaCr": None, "overseasCr": None},
            "week3": {"label": "Week 3 (Days 15–21)", "worldwideCr": None, "indiaCr": None, "overseasCr": None},
            "week4": {"label": "Week 4 (Days 22–28)", "worldwideCr": None, "indiaCr": None, "overseasCr": None},
        },
    )

    extracted: dict[str, Any] = {"weeks": {}}
    source_used = None

    for url in TARGET_URLS:
        try:
            html = fetch_page(url)
            if "ramayana" in html.lower():
                parsed = parse_box_office_text(html)
                if parsed.get("worldwideGrossCr") or parsed.get("weeks"):
                    extracted = parsed
                    source_used = url
                    break
        except Exception:
            continue

    # Update weekly numbers if newly scraped
    for wk_key, val in extracted.get("weeks", {}).items():
        if wk_key in weekly and val is not None:
            weekly[wk_key]["worldwideCr"] = val
            weekly[wk_key]["indiaCr"] = round(val * 0.78, 2)
            weekly[wk_key]["overseasCr"] = round(val * 0.22, 2)

    # Update top-level totals only if valid new figures were found
    prev_feed = data.get("automatedFeed", {})
    ww_total = extracted.get("worldwideGrossCr") or prev_feed.get("worldwideGrossCr", 0)
    ind_total = extracted.get("indiaGrossCr") or prev_feed.get("indiaGrossCr", 0)
    ov_total = extracted.get("overseasGrossCr") or prev_feed.get("overseasGrossCr", 0)

    data["automatedFeed"] = {
        "status": "success" if source_used else "retained_snapshot",
        "sourceUrl": source_used or "retained",
        "fetchedAt": fetched_at,
        "worldwideGrossCr": ww_total,
        "indiaGrossCr": ind_total,
        "overseasGrossCr": ov_total,
    }

    data["worldwideAllDates"] = {
        "scope": "lifetime_total",
        "currency": "INR",
        "status": "calculated",
        "worldwideGrossCr": ww_total,
        "indiaGrossCr": ind_total,
        "overseasGrossCr": ov_total,
        "calculatedAt": fetched_at,
    }

    data["lastChecked"] = fetched_at
    data["lastUpdated"] = fetched_at

    atomic_write_json(DATA_FILE, data)
    print(f"Update complete: WW={ww_total} Cr, Weeks={extracted.get('weeks')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
