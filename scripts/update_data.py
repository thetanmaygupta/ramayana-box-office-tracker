#!/usr/bin/env python3
"""Fetch Ramayana Day 1 advance-booking figures from BoxOfficeIndia24.

Best-effort HTML scraper, not an official API. Only source-labelled values are
updated. Missing or unrecognised values retain the last stored value.
"""

import json
import re
import sys
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data.json"
SOURCE_URL = (
    "https://www.boxofficeindia24.com/quicknews/"
    "ramayana-day-1-advance-booking-20261105"
)
IST = ZoneInfo("Asia/Kolkata")


class TextExtractor(HTMLParser):
    """Collect visible text, skipping scripts, styles and embedded SVG."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data):
        if not self.skip_depth and data.strip():
            self.parts.append(data.strip())


def fetch_page():
    request = Request(
        SOURCE_URL,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; RamayanaBoxOfficeTracker/1.1)"
            ),
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"Source returned HTTP {response.status}")
        return response.read().decode("utf-8", errors="replace")


def visible_text(raw):
    parser = TextExtractor()
    parser.feed(raw)
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def number(pattern, text):
    match = re.search(pattern, text, re.I)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except (ValueError, IndexError):
        return None


def integer(pattern, text):
    value = number(pattern, text)
    return int(value) if value is not None else None


def parse_page(raw):
    text = visible_text(raw)

    if "Ramayana Day 1 Advance Booking" not in text:
        raise RuntimeError("Expected Ramayana Day 1 report was not found.")

    # Match the source's explicit Key Highlights format first.
    india_gross = number(
        r"India\s*:\s*₹?\s*([\d,]+(?:\.\d+)?)\s*Cr"
        r"\s*gross\s+from\s+([\d,]+)\s+tickets?"
        r"\s+across\s+([\d,]+)\s+shows?",
        text,
    )
    tickets = None
    shows = None

    if india_gross is not None:
        highlight = re.search(
            r"India\s*:\s*₹?\s*([\d,]+(?:\.\d+)?)\s*Cr"
            r"\s*gross\s+from\s+([\d,]+)\s+tickets?"
            r"\s+across\s+([\d,]+)\s+shows?",
            text,
            re.I,
        )
        if highlight:
            tickets = int(highlight.group(2).replace(",", ""))
            shows = int(highlight.group(3).replace(",", ""))

    # Fallback labels are deliberately explicit to reduce accidental matches.
    if india_gross is None:
        india_gross = number(
            r"(?:India\s+gross|India\s+Day\s*1\s+gross|India\s+gross\s+"
            r"(?:excluding|without)\s+blocked\s+seats)\s*[:\-]?\s*"
            r"₹?\s*([\d,]+(?:\.\d+)?)\s*(?:Cr|Crore)",
            text,
        )
    if tickets is None:
        tickets = integer(
            r"(?:India[^.]{0,100}?)?([\d,]+)\s+tickets?\b",
            text,
        )
    if shows is None:
        shows = integer(
            r"(?:India[^.]{0,100}?)?([\d,]+)\s+shows?\b",
            text,
        )

    # Only parse a block-seat figure when the label directly precedes it.
    blocks_gross = number(
        r"(?:WITH\s+BLOCKS|GROSS\s+INCLUDING\s+(?:BLOCKED\s+)?SEATS?|"
        r"INCLUDING\s+(?:BLOCKED\s+)?SEATS?)\s*[:\-]?\s*"
        r"₹?\s*([\d,]+(?:\.\d+)?)\s*(?:Cr|Crore)",
        text,
    )
    # Also support common source phrasing "₹X Cr with blocks".
    if blocks_gross is None:
        blocks_gross = number(
            r"₹?\s*([\d,]+(?:\.\d+)?)\s*(?:Cr|Crore)"
            r"\s*(?:gross\s*)?(?:with|including)\s+(?:blocked\s+)?seats?",
            text,
        )

    # Excluding-blocks metrics are populated only when explicitly labelled.
    india_ex_blocks = number(
        r"(?:India\s+gross\s+)?(?:excluding|without)\s+(?:the\s+)?"
        r"(?:blocked\s+)?seats?\s*[:\-]?\s*₹?\s*"
        r"([\d,]+(?:\.\d+)?)\s*(?:Cr|Crore)",
        text,
    )
    tickets_ex_blocks = integer(
        r"([\d,]+)\s+tickets?\s*(?:excluding|without)\s+"
        r"(?:the\s+)?(?:blocked\s+)?seats?",
        text,
    )

    occupancy = number(
        r"(?:occupancy|occupancy\s+rate)\s*[:\-]?\s*([\d,.]+)\s*%",
        text,
    )

    # Parse a source timestamp if one is displayed.
    updated = re.search(
        r"(?:last\s+updated|updated\s+at)\s*[:\-]?\s*"
        r"(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}(?:\s*IST)?)",
        text,
        re.I,
    )
    source_updated = updated.group(1) if updated else None

    # Optional metrics: never infer overseas/worldwide from other totals.
    worldwide = number(
        r"(?:worldwide|world\s+wide)\s+(?:advance\s+booking\s+)?"
        r"(?:gross\s*)?[:\-]?\s*₹?\s*([\d,]+(?:\.\d+)?)\s*(?:Cr|Crore)",
        text,
    )
    overseas = number(
        r"overseas\s+(?:advance\s+booking\s+)?(?:gross\s*)?[:\-]?\s*"
        r"₹?\s*([\d,]+(?:\.\d+)?)\s*(?:Cr|Crore)",
        text,
    )

    if india_gross is None:
        raise RuntimeError("India gross was not found with a recognised label.")
    if tickets is None or not (0 < tickets < 100_000_000):
        raise RuntimeError("Ticket count missing or outside expected range.")
    if shows is None or not (0 < shows < 1_000_000):
        raise RuntimeError("Show count missing or outside expected range.")
    if not (0 < india_gross < 1000):
        raise RuntimeError(f"India gross outside expected range: {india_gross}")
    if blocks_gross is not None and blocks_gross < india_gross:
        raise RuntimeError("Gross including blocked seats is below India gross.")
    if india_ex_blocks is not None and india_ex_blocks > india_gross:
        raise RuntimeError("Excluding-blocks gross exceeds the India gross.")
    if occupancy is not None and not (0 <= occupancy <= 100):
        raise RuntimeError(f"Occupancy outside expected range: {occupancy}")
    if worldwide is not None and worldwide < india_gross:
        raise RuntimeError("Worldwide gross is below India gross.")

    result = {
        "indiaGrossCr": round(india_gross, 2),
        "tickets": tickets,
        "shows": shows,
    }
    optional = {
        "indiaGrossExcludingBlocksCr": india_ex_blocks,
        "indiaGrossIncludingBlocksCr": blocks_gross,
        "ticketsExcludingBlocks": tickets_ex_blocks,
        "occupancyPct": occupancy,
        "sourceUpdatedText": source_updated,
        "worldwideCr": worldwide,
        "overseasCr": overseas,
    }
    for key, value in optional.items():
        if value is not None:
            result[key] = round(value, 2) if isinstance(value, float) else value

    return result


def main():
    if not DATA_FILE.exists():
        raise RuntimeError(f"Missing data file: {DATA_FILE}")

    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    feed = data.setdefault("automatedFeed", {})
    fetched_at = datetime.now(IST).isoformat(timespec="seconds")

    feed["sourceName"] = "BoxOfficeIndia24 — Ramayana Day 1 advance booking"
    feed["sourceUrl"] = SOURCE_URL

    try:
        parsed = parse_page(fetch_page())

        # Update only fields found and validated in this scrape. Existing
        # fields are deliberately retained when the page omits a metric.
        feed.update(parsed)
        feed["status"] = "success"
        feed["fetchedAt"] = fetched_at
        feed.pop("lastAttemptAt", None)
        feed["message"] = (
            "Source-reported figures parsed from BoxOfficeIndia24; "
            "not producer-confirmed. Missing metrics were not inferred."
        )
        print(json.dumps(
            {"status": "success", "fetchedAt": fetched_at, "parsed": parsed},
            ensure_ascii=False,
            indent=2,
        ))
    except Exception as exc:
        feed["status"] = "stale" if feed.get("fetchedAt") else "error"
        feed["lastAttemptAt"] = fetched_at
        feed["message"] = (
            f"Automatic check failed ({type(exc).__name__}: {exc}). "
            "Previous successful values were retained."
        )
        DATA_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(feed["message"], file=sys.stderr)
        raise

    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
