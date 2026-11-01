#!/usr/bin/env python3
"""Best-effort parser for Sacnilk's public Ramayana advance-booking page."""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data.json"
SOURCE_URL = (
    "https://www.sacnilk.com/"
    "livetracker/ramayana_part_1_2026_Advance-Booking-Live"
)
IST = ZoneInfo("Asia/Kolkata")


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.meta = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)

        if tag in ("script", "style", "noscript", "svg"):
            self.skip += 1

        if tag == "meta" and attrs.get("content"):
            self.meta.append(attrs["content"])

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(data.strip())


def fetch_page():
    req = Request(
        SOURCE_URL,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; "
                "RamayanaBoxOfficeTracker/1.0; "
                "+https://github.com/thetanmaygupta/"
                "ramayana-box-office-tracker)"
            ),
            "Accept": "text/html,application/xhtml+xml",
        },
    )

    with urlopen(req, timeout=25) as response:
        if response.status != 200:
            raise RuntimeError(f"Source returned HTTP {response.status}")

        return response.read().decode("utf-8", errors="replace")


def number(value):
    return float(value.replace(",", ""))


def parse_page(raw):
    parser = TextExtractor()
    parser.feed(raw)

    text = re.sub(
        r"\s+",
        " ",
        " ".join(parser.meta + parser.parts),
    )

    # Try several ways of locating the overall India advance gross.
    patterns = [
        r"overall\s+advance\s*\(live\).*?"
        r"(?:₹|&#8377;|&#x20b9;|rs\.?\s*)"
        r"\s*([\d,]+(?:\.\d+)?)\s*Cr",

        r"Ramayana\s+advance\s+booking"
        r".{0,1500}?"
        r"(?:₹|&#8377;|&#x20b9;|rs\.?\s*)"
        r"\s*([\d,]+(?:\.\d+)?)\s*Cr",

        r"(?:India|all\s+India).*?"
        r"(?:gross|advance).*?"
        r"(?:₹|&#8377;|&#x20b9;|rs\.?\s*)"
        r"\s*([\d,]+(?:\.\d+)?)\s*Cr",
    ]

    gross = None
    match = None

    for pattern in patterns:
        candidate_match = re.search(
            pattern,
            text,
            re.I | re.S,
        )

        if candidate_match:
            candidate = number(candidate_match.group(1))

            if 0 < candidate < 1000:
                gross = candidate
                match = candidate_match
                break

    if gross is None or match is None:
        raise RuntimeError(
            "Could not identify the overall India advance gross. "
            "Page text preview: " + text[:500]
        )

    # Search nearby text for supporting figures.
    context = text[
        max(0, match.start() - 200):
        match.start() + 1800
    ]

    gross_values = re.findall(
        r"(?:₹|rs\.?\s*)\s*([\d,]+(?:\.\d+)?)\s*Cr",
        context,
        re.I,
    )

    excluding = (
        number(gross_values[1])
        if len(gross_values) > 1
        and re.search(r"without\s+block", context, re.I)
        else None
    )

    ticket_match = re.search(
        r"([\d,.]+)\s*(lakh|lac)"
        r"(?:\s*\[\s*([\d,.]+)\s*(?:lakh|lac)?\s*\])?"
        r"\s*tickets?\s+booked",
        context,
        re.I,
    )

    if ticket_match:
        tickets = round(
            number(ticket_match.group(1)) * 100000
        )

        tickets_excl = (
            round(number(ticket_match.group(3)) * 100000)
            if ticket_match.group(3)
            else None
        )

    else:
        ticket_match = re.search(
            r"([\d,]+)\s*tickets?\s+booked",
            context,
            re.I,
        )

        tickets = (
            int(ticket_match.group(1).replace(",", ""))
            if ticket_match
            else None
        )

        tickets_excl = None

    shows_match = re.search(
        r"([\d,]+)\s*shows?\b",
        context,
        re.I,
    )

    shows = (
        int(shows_match.group(1).replace(",", ""))
        if shows_match
        else None
    )

    occupancy_match = re.search(
        r"([\d,.]+)\s*%\s*(?:occupancy)?",
        context,
        re.I,
    )

    occupancy = (
        number(occupancy_match.group(1))
        if occupancy_match
        else None
    )

    source_updated_match = re.search(
        r"(?:Updated|as of)\s+\d{1,2}:\d{2}\s*"
        r"(?:AM|PM)\s*IST",
        text,
        re.I,
    )

    source_updated = (
        source_updated_match.group(0)
        if source_updated_match
        else None
    )

    if not (0 < gross < 1000):
        raise RuntimeError(
            f"Parsed gross outside sanity range: {gross}"
        )

    if excluding is not None and not (0 <= excluding <= gross):
        excluding = None

    if tickets is not None and not (0 < tickets < 100_000_000):
        tickets = None

    return {
        "indiaGrossCr": round(gross, 2),
        "indiaGrossExcludingBlocksCr": (
            round(excluding, 2)
            if excluding is not None
            else None
        ),
        "tickets": tickets,
        "ticketsExcludingBlocks": tickets_excl,
        "shows": shows,
        "occupancyPct": occupancy,
        "sourceUpdatedText": source_updated,
    }


def main():
    data = json.loads(
        DATA_FILE.read_text(encoding="utf-8")
    )

    feed = data.setdefault("automatedFeed", {})

    feed["sourceName"] = (
        "Sacnilk – Ramayana advance booking live tracker"
    )
    feed["sourceUrl"] = SOURCE_URL

    fetched_at = (
        datetime.now(timezone.utc)
        .astimezone(IST)
        .isoformat(timespec="seconds")
    )

    try:
        parsed = parse_page(fetch_page())

        feed.update(parsed)
        feed["status"] = "success"
        feed["fetchedAt"] = fetched_at

        feed["message"] = (
            "Latest page parsed successfully. Figures are "
            "source-reported estimates, not producer-confirmed results."
        )

        print(
            json.dumps(
                {
                    "status": "success",
                    "fetchedAt": fetched_at,
                    **parsed,
                },
                indent=2,
            )
        )

    except Exception as exc:
        feed["status"] = (
            "stale" if feed.get("fetchedAt") else "error"
        )

        feed["lastAttemptAt"] = fetched_at

        feed["message"] = (
            f"Automatic check failed ({type(exc).__name__}): {exc}. "
            "Previous successful values, if any, were retained."
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
