#!/usr/bin/env python3
"""
ReelMetrics — Ramayana Box Office Data Updater

Primary sources:
  1. BoxOfficeIndia24
  2. Sacnilk (optional URL configuration required)

Secondary validation:
  The Week, Financial Express, Koimoi

Advance bookings and actual theatrical collections are stored separately.

This is a best-effort HTML scraper, not an official API.
Unrecognised or missing values are never inferred.
"""

import json
import re
import sys
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data.json"
IST = ZoneInfo("Asia/Kolkata")

USER_AGENT = (
    "Mozilla/5.0 (compatible; ReelMetrics/2.0)"
)

BOI24_DAILY_URLS = {
    "2026-11-05": (
        "https://www.boxofficeindia24.com/quicknews/"
        "ramayana-day-1-advance-booking-20261105"
    ),
    "2026-11-06": (
        "https://www.boxofficeindia24.com/quicknews/"
        "ramayana-day-2-advance-booking-20261106"
    ),
    "2026-11-07": (
        "https://www.boxofficeindia24.com/quicknews/"
        "ramayana-day-3-advance-booking-20261107"
    ),
}

BOI24_OVERSEAS_URL = (
    "https://www.boxofficeindia24.com/quicknews/"
    "ramayana-day-1-advance-booking-20261105?view=overseas"
)

# Add the exact Sacnilk tracker URL here when confirmed.
# Do not guess or substitute an unrelated page.
SACNILK_URL = ""


# ---------------------------------------------------------
# SOURCE REGISTRY
# ---------------------------------------------------------

SOURCES = [
    {
        "name": "Sacnilk",
        "tier": "primary",
        "url": "https://www.sacnilk.com/",
        "role": "India advance-booking cross-check",
    },
    {
        "name": "BoxOfficeIndia24",
        "tier": "primary",
        "url": "https://www.boxofficeindia24.com/",
        "role": "India, overseas and worldwide advance bookings",
    },
    {
        "name": "The Week",
        "tier": "secondary",
        "url": "https://www.theweek.in/",
        "role": "Editorial validation",
    },
    {
        "name": "Financial Express",
        "tier": "secondary",
        "url": "https://www.financialexpress.com/",
        "role": "Editorial validation",
    },
    {
        "name": "Koimoi",
        "tier": "secondary",
        "url": "https://www.koimoi.com/",
        "role": "Trade-report validation",
    },
]


# ---------------------------------------------------------
# HTML EXTRACTION
# ---------------------------------------------------------

class TextExtractor(HTMLParser):
    """Extract visible text from HTML."""

    SKIP_TAGS = {
        "script",
        "style",
        "noscript",
        "svg",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0
        self.skip_tags = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()

        if tag in self.SKIP_TAGS:
            self.skip_tags.append(tag)
            self.skip_depth += 1

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag in self.SKIP_TAGS and self.skip_depth:
            if tag in self.skip_tags:
                self.skip_tags.remove(tag)
                self.skip_depth -= 1

    def handle_data(self, data):
        if not self.skip_depth and data.strip():
            self.parts.append(data.strip())


def visible_text(raw):
    parser = TextExtractor()
    parser.feed(raw)

    return re.sub(
        r"\s+",
        " ",
        " ".join(parser.parts)
    ).strip()


# ---------------------------------------------------------
# HTTP
# ---------------------------------------------------------

def fetch_page(url):
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
    )

    with urlopen(request, timeout=30) as response:

        if response.status != 200:
            raise RuntimeError(
                f"HTTP error: {response.status}"
            )

        return response.read().decode(
            "utf-8",
            errors="replace"
        )


# ---------------------------------------------------------
# NUMBER PARSING
# ---------------------------------------------------------

def number(pattern, text):
    match = re.search(pattern, text, re.I)

    if not match:
        return None

    try:
        return float(
            match.group(1).replace(",", "")
        )
    except (ValueError, IndexError):
        return None


def integer(pattern, text):
    value = number(pattern, text)

    if value is None:
        return None

    return int(value)


def first_number(patterns, text):
    for pattern in patterns:
        value = number(pattern, text)

        if value is not None:
            return value

    return None


def first_integer(patterns, text):
    for pattern in patterns:
        value = integer(pattern, text)

        if value is not None:
            return value

    return None


# ---------------------------------------------------------
# COMMON SOURCE METADATA
# ---------------------------------------------------------

def extract_timestamp(text):
    patterns = [
        r"(?:last\s+updated|updated\s+at)"
        r"\s*[:\-]?\s*"
        r"(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}"
        r"(?:\s*IST)?)",

        r"Updated\s+(\d{1,2}:\d{2}\s*IST)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.I)

        if match:
            return match.group(1)

    return None


def extract_metrics(text):
    return {
        "tickets": first_integer([
            r"gross\s+from\s+([\d,]+)\s+tickets?",
            r"([\d,]+)\s+tickets?\b",
        ], text),

        "shows": first_integer([
            r"across\s+([\d,]+)\s+shows?",
            r"([\d,]+)\s+shows?\b",
        ], text),

        "occupancyPct": first_number([
            r"occupancy(?:\s+rate)?\s*[:\-]?\s*"
            r"([\d,.]+)\s*%",
        ], text),
    }


# ---------------------------------------------------------
# INDIA ADVANCE BOOKING
# ---------------------------------------------------------

def extract_india(text):
    patterns = [
        r"India\s*:\s*₹?\s*"
        r"([\d,]+(?:\.\d+)?)\s*Cr",

        r"(?:India\s+gross|India\s+Day\s*1\s+gross)"
        r"\s*[:\-]?\s*₹?\s*"
        r"([\d,]+(?:\.\d+)?)\s*(?:Cr|Crore)",
    ]

    return first_number(patterns, text)


# ---------------------------------------------------------
# WORLDWIDE AND OVERSEAS ADVANCE
# ---------------------------------------------------------

def extract_worldwide(text):
    patterns = [
        r"Worldwide\s*:\s*₹?\s*"
        r"([\d,]+(?:\.\d+)?)\s*Cr",

        r"Worldwide\s+(?:advance\s+booking\s+)?"
        r"(?:gross\s*)?[:\-]?\s*₹?\s*"
        r"([\d,]+(?:\.\d+)?)\s*(?:Cr|Crore)",
    ]

    return first_number(patterns, text)


def extract_overseas(text):
    patterns = [
        r"overseas\s*[:\-]?\s*₹?\s*"
        r"([\d,]+(?:\.\d+)?)\s*Cr",

        r"overseas\s+(?:advance\s+booking\s+)?"
        r"(?:gross\s*)?[:\-]?\s*₹?\s*"
        r"([\d,]+(?:\.\d+)?)\s*(?:Cr|Crore)",
    ]

    return first_number(patterns, text)


# ---------------------------------------------------------
# DAY-SPECIFIC REPORT PARSER
# ---------------------------------------------------------

def parse_day_page(raw, date, day, url):
    text = visible_text(raw)

    if "Ramayana" not in text:
        raise RuntimeError(
            "Ramayana report not detected."
        )

    india = extract_india(text)
    worldwide = extract_worldwide(text)
    overseas = extract_overseas(text)

    metrics = extract_metrics(text)

    if india is None:
        raise RuntimeError(
            "India gross was not found."
        )

    if not 0 < india < 1000:
        raise RuntimeError(
            "India gross outside expected range."
        )

    if worldwide is not None and worldwide < india:
        raise RuntimeError(
            "Worldwide gross is below India gross."
        )

    if overseas is not None and overseas > 1000:
        raise RuntimeError(
            "Overseas gross outside expected range."
        )

    # If the publisher explicitly provides all three values,
    # check whether the components reconcile.
    if (
        worldwide is not None
        and overseas is not None
        and abs(worldwide - (india + overseas)) > 0.15
    ):
        raise RuntimeError(
            "Worldwide total does not reconcile with "
            "India and overseas components."
        )

    result = {
        "date": date,
        "day": day,
        "indiaGrossCr": round(india, 2),
        "sourceName": "BoxOfficeIndia24",
        "sourceUrl": url,
        "sourceUpdatedText": extract_timestamp(text),
        "fetchedAt": datetime.now(IST).isoformat(
            timespec="seconds"
        ),
        "scope": "single_day_advance",
    }

    for key, value in metrics.items():
        if value is not None:
            result[key] = value

    if worldwide is not None:
        result["worldwideGrossCr"] = round(
            worldwide, 2
        )

    if overseas is not None:
        result["overseasGrossCr"] = round(
            overseas, 2
        )

    result["worldwideVerifiedFromPublisher"] = (
        worldwide is not None
        and overseas is not None
    )

    return result


# ---------------------------------------------------------
# OVERSEAS ALL-DATES VIEW
# ---------------------------------------------------------

def parse_overseas_page(raw):
    text = visible_text(raw)

    gross = first_number([
        r"OVERSEAS\s+ADVANCE[^₹]{0,120}"
        r"₹\s*([\d,]+(?:\.\d+)?)\s*Cr",
    ], text)

    if gross is None:
        raise RuntimeError(
            "Overseas all-dates gross not found."
        )

    metrics = extract_metrics(text)

    tickets = first_integer([
        r"gross\s+from\s+([\d,]+)\s+tickets",
    ], text)

    shows = first_integer([
        r"([\d,]+)\s+SHOWS",
    ], text)

    cinemas = first_integer([
        r"([\d,]+)\s+CINEMAS",
    ], text)

    countries = first_integer([
        r"([\d,]+)\s+COUNTRIES",
    ], text)

    occupancy = first_number([
        r"([\d.]+)\s*%\s*OCCUPANCY",
    ], text)

    date_range = None

    match = re.search(
        r"OVERSEAS\s+ADVANCE\s*[·|]\s*"
        r"([\d]{1,2}\s*[–-]\s*[\d]{1,2}\s+[A-Za-z]+)",
        text,
        re.I,
    )

    if match:
        date_range = match.group(1)

    return {
        "grossCr": round(gross, 2),
        "tickets": tickets,
        "shows": shows,
        "cinemas": cinemas,
        "countries": countries,
        "occupancyPct": occupancy,
        "dateRange": date_range,
        "sourceName": "BoxOfficeIndia24",
        "sourceUrl": BOI24_OVERSEAS_URL,
        "sourceUpdatedText": extract_timestamp(text),
        "fetchedAt": datetime.now(IST).isoformat(
            timespec="seconds"
        ),
        "scope": "overseas_all_dates",
        "note": (
            "Overseas all-dates advance booking. "
            "Not worldwide Day 1 or actual collection."
        ),
    }


# ---------------------------------------------------------
# SOURCE HIERARCHY
# ---------------------------------------------------------

def configure_source_metadata(data):
    data["sources"] = SOURCES

    data["sourcePolicy"] = {
        "primary": [
            "Sacnilk",
            "BoxOfficeIndia24",
        ],
        "secondary": [
            "The Week",
            "Financial Express",
            "Koimoi",
        ],
        "actualCollections": (
            "Separate dataset. Never combine with advance bookings."
        ),
        "independenceRule": (
            "Secondary publications citing a primary tracker "
            "are not independent measurements."
        ),
        "comparisonRule": (
            "Compare matching dates, territories, gross/net "
            "definitions, blocked-seat treatment and timestamps."
        ),
    }

    data.setdefault(
        "actualCollections",
        {
            "status": "awaiting_verified_trade_reports",
            "records": [],
            "note": (
                "Actual theatrical collections are separate "
                "from advance-booking figures."
            ),
        },
    )


# ---------------------------------------------------------
# MAIN UPDATE
# ---------------------------------------------------------

def main():
    if not DATA_FILE.exists():
        raise RuntimeError(
            f"Missing data.json: {DATA_FILE}"
        )

    data = json.loads(
        DATA_FILE.read_text(encoding="utf-8")
    )

    fetched_at = datetime.now(IST).isoformat(
        timespec="seconds"
    )

    warnings = []

    # Maintain existing website-compatible feed.
    feed = data.setdefault("automatedFeed", {})

    # -----------------------------------------------------
    # 1. DAY-WISE BOI24 ADVANCE BOOKINGS
    # -----------------------------------------------------

    day_data = data.setdefault(
        "dayWiseAdvance",
        {},
    )

    for day, (date, url) in enumerate(
        BOI24_DAILY_URLS.items(),
        start=1,
    ):
        try:
            parsed = parse_day_page(
                fetch_page(url),
                date,
                day,
                url,
            )

            # Update only a successfully parsed date.
            day_data[date] = parsed

            print(
                f"Day {day}: successfully updated."
            )

        except Exception as exc:
            warnings.append(
                f"Day {day} failed: {exc}"
            )

            print(
                f"Day {day} retained previous data: {exc}",
                file=sys.stderr,
            )

    # -----------------------------------------------------
    # 2. OVERSEAS ALL-DATES SNAPSHOT
    # -----------------------------------------------------

    try:
        overseas = parse_overseas_page(
            fetch_page(BOI24_OVERSEAS_URL)
        )

        data["overseasAllDates"] = overseas

        print(
            "Overseas all-dates snapshot updated."
        )

    except Exception as exc:
        warnings.append(
            f"Overseas page failed: {exc}"
        )

        print(
            f"Overseas snapshot retained: {exc}",
            file=sys.stderr,
        )

    # -----------------------------------------------------
    # 3. LEGACY DAY 1 FEED COMPATIBILITY
    # -----------------------------------------------------

    day1 = day_data.get("2026-11-05", {})

    if day1.get("indiaGrossCr") is not None:
        feed["indiaGrossCr"] = day1["indiaGrossCr"]

    if day1.get("tickets") is not None:
        feed["tickets"] = day1["tickets"]

    if day1.get("shows") is not None:
        feed["shows"] = day1["shows"]

    feed["sourceName"] = (
        "BoxOfficeIndia24 — Ramayana Day 1"
    )

    feed["sourceUrl"] = (
        BOI24_DAILY_URLS["2026-11-05"]
    )

    # Do not use overseas all-dates as Day 1 overseas.
    # Remove ambiguous legacy values.
    feed.pop("overseasCr", None)

    if day1:
        feed["status"] = "success"
        feed["fetchedAt"] = day1.get(
            "fetchedAt",
            fetched_at,
        )

        feed["message"] = (
            "Day 1 India advance booking from "
            "BoxOfficeIndia24. Worldwide total is only "
            "available when explicitly reported with "
            "matching Day 1 scope."
        )

    # -----------------------------------------------------
    # 4. SOURCE REGISTRY AND POLICY
    # -----------------------------------------------------

    configure_source_metadata(data)

    # -----------------------------------------------------
    # 5. SACNILK CONFIGURATION STATUS
    # -----------------------------------------------------

    data["sourceValidation"] = {
        "sacnilk": {
            "status": (
                "configured"
                if SACNILK_URL
                else "awaiting_exact_tracker_url"
            ),
            "url": SACNILK_URL or None,
            "note": (
                "Sacnilk is registered as a primary source. "
                "Automated comparison requires its exact "
                "Ramayana tracker URL and a compatible parser."
            ),
        },
        "secondary": {
            "status": "registered_for_manual_validation",
            "note": (
                "The Week, Financial Express and Koimoi "
                "are not counted as independent measurements "
                "when they cite a primary tracker."
            ),
        },
    }

    # -----------------------------------------------------
    # 6. UPDATE STATUS
    # -----------------------------------------------------

    data["lastUpdated"] = fetched_at

    data["dataStatus"] = (
        "source_scoped_snapshot"
        if not warnings
        else "partial_source_update"
    )

    data["ingestion"] = {
        "source": "BoxOfficeIndia24",
        "fetchedAt": fetched_at,
        "refreshIntervalMinutes": 30,
        "status": (
            "success"
            if not warnings
            else "partial"
        ),
        "warnings": warnings,
        "note": (
            "Successful page parsing does not guarantee "
            "publisher estimate accuracy or producer confirmation."
        ),
    }

    # -----------------------------------------------------
    # 7. WRITE DATA SAFELY
    # -----------------------------------------------------

    DATA_FILE.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            data["ingestion"],
            ensure_ascii=False,
            indent=2,
        )
    )


def fetch_page(url):
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
    )

    with urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(
                f"HTTP error: {response.status}"
            )

        return response.read().decode(
            "utf-8",
            errors="replace",
        )


if __name__ == "__main__":
    main()
