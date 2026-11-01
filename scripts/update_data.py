#!/usr/bin/env python3
"""Fetch Ramayana Day 1 advance-booking data from BoxOfficeIndia24."""

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
    "https://www.boxofficeindia24.com/quicknews/"
    "ramayana-day-1-advance-booking-20261105"
)

IST = ZoneInfo("Asia/Kolkata")


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(data.strip())


def fetch_page():
    request = Request(
        SOURCE_URL,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(compatible; RamayanaBoxOfficeTracker/1.0)"
            ),
            "Accept": "text/html,application/xhtml+xml",
        },
    )

    with urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(
                f"Source returned HTTP {response.status}"
            )

        return response.read().decode("utf-8", errors="replace")


def parse_page(raw):
    parser = TextExtractor()
    parser.feed(raw)

    text = re.sub(r"\s+", " ", " ".join(parser.parts))

    # Require the expected Day 1 report before parsing figures.
    if "Ramayana Day 1 Advance Booking" not in text:
        raise RuntimeError(
            "Expected Ramayana Day 1 report was not found."
        )

    # Extract India gross, tickets and shows from Key highlights.
    india_match = re.search(
        r"India\s*:\s*₹\s*([\d,]+(?:\.\d+)?)\s*Cr"
        r"\s*gross\s+from\s+([\d,]+)\s+tickets?"
        r"\s+across\s+([\d,]+)\s+shows?",
        text,
        re.I,
    )

    if not india_match:
        raise RuntimeError(
            "Could not identify India gross, tickets and shows. "
            "The source layout may have changed."
        )

    india_gross = float(
        india_match.group(1).replace(",", "")
    )
    tickets = int(india_match.group(2).replace(",", ""))
    shows = int(india_match.group(3).replace(",", ""))

    # Extract the separate gross that includes block seats.
    blocks_match = re.search(
        r"WITH\s+BLOCKS\s*₹\s*"
        r"([\d,]+(?:\.\d+)?)\s*Cr",
        text,
        re.I,
    )

    gross_with_blocks = (
        float(blocks_match.group(1).replace(",", ""))
        if blocks_match
        else None
    )

    # Extract occupancy if the expected label is present.
    occupancy_match = re.search(
        r"OCCUPANCY\s+([\d,.]+)\s*%",
        text,
        re.I,
    )

    occupancy = (
        float(occupancy_match.group(1).replace(",", ""))
        if occupancy_match
        else None
    )

    # Example: Last updated: 2026-11-02 04:52 IST
    updated_match = re.search(
        r"Last updated:\s*"
        r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s+IST)",
        text,
        re.I,
    )

    source_updated = (
        updated_match.group(1)
        if updated_match
        else None
    )

    # Sanity checks prevent obviously incorrect values being published.
    if not (0 < india_gross < 1000):
        raise RuntimeError(
            f"India gross outside expected range: {india_gross}"
        )

    if not (0 < tickets < 100_000_000):
        raise RuntimeError(
            f"Ticket count outside expected range: {tickets}"
        )

    if not (0 < shows < 1_000_000):
        raise RuntimeError(
            f"Show count outside expected range: {shows}"
        )

    if (
        gross_with_blocks is not None
        and gross_with_blocks < india_gross
    ):
        raise RuntimeError(
            "Gross including block seats is below India gross."
        )

    if occupancy is not None and not (0 <= occupancy <= 100):
        raise RuntimeError(
            f"Occupancy outside expected range: {occupancy}"
        )

    return {
        "indiaGrossCr": round(india_gross, 2),
        "indiaGrossExcludingBlocksCr": None,
        "indiaGrossIncludingBlocksCr": (
            round(gross_with_blocks, 2)
            if gross_with_blocks is not None
            else None
        ),
        "tickets": tickets,
        "ticketsExcludingBlocks": None,
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
        "BoxOfficeIndia24 — Ramayana Day 1 advance booking"
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
        feed.pop("lastAttemptAt", None)
        feed["message"] = (
            "Day 1 advance-booking figures parsed from "
            "BoxOfficeIndia24. These are source-reported "
            "figures, not producer-confirmed results."
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
            f"Automatic check failed "
            f"({type(exc).__name__}): {exc}. "
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
