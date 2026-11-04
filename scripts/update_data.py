#!/usr/bin/env python3
"""
ReelMetrics — Ramayana Box Office Data Updater

Primary sources:
  1. BoxOfficeIndia24
  2. Sacnilk (optional URL configuration required)

Audience data:
  3. TMDB API — movie rating, vote count and written reviews

Advance bookings and actual theatrical collections are stored separately.

This is a best-effort HTML scraper plus TMDB API integration.
Missing values are never inferred. TMDB failures do not stop
box-office ingestion or overwrite the last successful review snapshot.
"""

import json
import os
import re
import sys
import tempfile
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data.json"
REVIEWS_FILE = ROOT / "reviews.json"

IST = ZoneInfo("Asia/Kolkata")

USER_AGENT = "Mozilla/5.0 (compatible; ReelMetrics/2.1)"

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

SACNILK_URL = ""

# Confirmed against the existing TMDB movie reference.
# The returned title is validated before accepting any data.
TMDB_MOVIE_ID = 656908
TMDB_API_BASE = "https://api.themoviedb.org/3"

TMDB_REVIEW_PAGES_MAX = 3
TMDB_REVIEW_TEXT_MAX_CHARS = 5000


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
# TIME AND SAFE JSON WRITING
# ---------------------------------------------------------

def now_ist():
    return datetime.now(IST).isoformat(timespec="seconds")


def atomic_write_json(path, payload):
    """Write JSON atomically to avoid corrupting an existing file."""

    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
            )
            handle.write("\n")

        os.replace(temporary_name, path)

    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


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
        " ".join(parser.parts),
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
            errors="replace",
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
        "fetchedAt": now_ist(),
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
        "fetchedAt": now_ist(),
        "scope": "overseas_all_dates",
        "note": (
            "Overseas all-dates advance booking. "
            "Not worldwide Day 1 or actual collection."
        ),
    }


# ---------------------------------------------------------
# TMDB API
# ---------------------------------------------------------

def tmdb_request(path, token, params=None):
    """Make an authenticated TMDB API request."""

    query = f"?{urlencode(params)}" if params else ""
    url = f"{TMDB_API_BASE}{path}{query}"

    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )

    with urlopen(request, timeout=25) as response:
        if response.status != 200:
            raise RuntimeError(
                f"TMDB returned HTTP {response.status}."
            )

        return json.loads(
            response.read().decode(
                "utf-8",
                errors="replace",
            )
        )


def fetch_tmdb_audience_data():
    """
    Fetch movie details and genuine TMDB written reviews.

    Does not invent review highlights or sentiment labels.
    """

    token = os.getenv(
        "TMDB_READ_ACCESS_TOKEN",
        "",
    ).strip()

    if not token:
        raise RuntimeError(
            "TMDB_READ_ACCESS_TOKEN is not configured."
        )

    # 1. Movie details
    movie = tmdb_request(
        f"/movie/{TMDB_MOVIE_ID}",
        token,
        {"language": "en-US"},
    )

    title = str(movie.get("title") or "").strip()
    original_title = str(
        movie.get("original_title") or ""
    ).strip()

    title_candidates = " ".join(
        value
        for value in (title, original_title)
        if value
    ).casefold()

    if "ramayana" not in title_candidates:
        raise RuntimeError(
            "TMDB movie ID validation failed. "
            "The returned title does not identify Ramayana."
        )

    # 2. Validate rating fields
    vote_average = movie.get("vote_average")
    vote_count = movie.get("vote_count")

    if (
        not isinstance(vote_average, (int, float))
        or not 0 <= float(vote_average) <= 10
    ):
        vote_average = None

    if (
        not isinstance(vote_count, int)
        or vote_count < 0
    ):
        vote_count = None

    # 3. Fetch written reviews
    first_page = tmdb_request(
        f"/movie/{TMDB_MOVIE_ID}/reviews",
        token,
        {
            "language": "en-US",
            "page": 1,
        },
    )

    total_review_results = max(
        0,
        int(first_page.get("total_results") or 0),
    )

    total_review_pages = max(
        0,
        int(first_page.get("total_pages") or 0),
    )

    pages_to_fetch = min(
        total_review_pages,
        TMDB_REVIEW_PAGES_MAX,
    )

    review_items = []

    for page_number in range(1, pages_to_fetch + 1):

        page_data = first_page

        if page_number > 1:
            page_data = tmdb_request(
                f"/movie/{TMDB_MOVIE_ID}/reviews",
                token,
                {
                    "language": "en-US",
                    "page": page_number,
                },
            )

        for item in page_data.get("results", []):

            if not isinstance(item, dict):
                continue

            content = str(
                item.get("content") or ""
            ).strip()

            if not content:
                continue

            author_details = (
                item.get("author_details") or {}
            )

            rating = author_details.get("rating")

            if (
                not isinstance(rating, (int, float))
                or not 0 <= float(rating) <= 10
            ):
                rating = None

            review_items.append({
                "id": item.get("id"),
                "author": item.get("author"),
                "username": author_details.get("username"),
                "rating": rating,
                "createdAt": item.get("created_at"),
                "updatedAt": item.get("updated_at"),
                "url": item.get("url"),
                "content": content[
                    :TMDB_REVIEW_TEXT_MAX_CHARS
                ],
                "contentTruncated": (
                    len(content) > TMDB_REVIEW_TEXT_MAX_CHARS
                ),
                "source": "TMDB",
            })

    fetched_at = now_ist()

    return {
        "movie": title or "Ramayana: Rise of a Legend",
        "status": "success",
        "source": "The Movie Database (TMDB)",
        "sourceUrl": (
            f"https://www.themoviedb.org/movie/{TMDB_MOVIE_ID}"
        ),
        "lastUpdated": fetched_at,

        # Existing website compatibility:
        # TMDB average is out of 10, so multiply by 10
        # for the existing 0–100 visual display.
        "tmdbScore": (
            round(float(vote_average) * 10, 1)
            if vote_average is not None
            else None
        ),

        "voteCount": vote_count,
        "reviewCount": total_review_results,

        # No fabricated editorial summaries.
        "highlights": [],
        "sentiment": None,
        "reviews": review_items,

        "message": (
            "TMDB rating and written-review data fetched. "
            "Sentiment analysis has not been performed."
        ),

        "tmdb": {
            "movieId": TMDB_MOVIE_ID,
            "title": title,
            "originalTitle": original_title,
            "overview": movie.get("overview"),
            "releaseDate": movie.get("release_date"),

            "voteAverage": (
                round(float(vote_average), 2)
                if vote_average is not None
                else None
            ),

            "voteCount": vote_count,
            "reviewCount": total_review_results,
            "reviewPagesFetched": pages_to_fetch,
            "reviewPagesAvailable": total_review_pages,
            "posterPath": movie.get("poster_path"),
            "backdropPath": movie.get("backdrop_path"),

            "genres": [
                genre.get("name")
                for genre in movie.get("genres", [])
                if (
                    isinstance(genre, dict)
                    and genre.get("name")
                )
            ],

            "apiBase": TMDB_API_BASE,
        },

        "internalAudit": {
            "tmdbMovieId": TMDB_MOVIE_ID,
            "tmdbReviewCount": total_review_results,
            "tmdbReviewRecordsFetched": len(review_items),
            "sentimentAnalysis": "not_run",
            "sentimentEvidence": (
                "Raw TMDB review text only. "
                "No sentiment labels generated."
            ),
        },
    }


def update_tmdb_reviews():
    """
    Update reviews.json independently.

    If TMDB fails, retain the previous successful reviews.json.
    """

    try:
        audience_data = fetch_tmdb_audience_data()

        atomic_write_json(
            REVIEWS_FILE,
            audience_data,
        )

        print(
            "TMDB audience data updated: "
            f"{audience_data.get('voteCount')} votes, "
            f"{audience_data.get('reviewCount')} reviews."
        )

        return {
            "status": "success",
            "fetchedAt": audience_data["lastUpdated"],
            "reviewCount": audience_data.get(
                "reviewCount", 0
            ),
            "voteCount": audience_data.get("voteCount"),
        }

    except Exception as exc:

        print(
            "TMDB update failed; existing reviews.json "
            f"was preserved: {exc}",
            file=sys.stderr,
        )

        return {
            "status": (
                "not_configured"
                if "not configured" in str(exc).lower()
                else "failed"
            ),
            "checkedAt": now_ist(),
            "warning": str(exc),
            "preservedPreviousData": REVIEWS_FILE.exists(),
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

    fetched_at = now_ist()
    warnings = []

    # -----------------------------------------------------
    # 1. TMDB AUDIENCE DATA
    # -----------------------------------------------------

    # This pipeline is deliberately independent.
    # Failure here must not stop box-office ingestion.

    audience_ingestion = update_tmdb_reviews()

    # -----------------------------------------------------
    # 2. DAY-WISE BOI24 ADVANCE BOOKINGS
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
    # 3. OVERSEAS ALL-DATES SNAPSHOT
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
    # 4. LEGACY DAY 1 FEED COMPATIBILITY
    # -----------------------------------------------------

    feed = data.setdefault(
        "automatedFeed",
        {},
    )

    day1 = day_data.get(
        "2026-11-05",
        {},
    )

    if day1.get("indiaGrossCr") is not None:
        feed["indiaGrossCr"] = day1["indiaGrossCr"]

    if day1.get("tickets") is not None:
        feed["tickets"] = day1["tickets"]

    if day1.get("shows") is not None:
        feed["shows"] = day1["shows"]

    if day1.get("occupancyPct") is not None:
        feed["occupancyPct"] = day1["occupancyPct"]

    feed["sourceName"] = (
        "BoxOfficeIndia24 — Ramayana Day 1"
    )

    feed["sourceUrl"] = (
        BOI24_DAILY_URLS["2026-11-05"]
    )

    # Do not use overseas all-dates as Day 1 overseas.
    feed.pop("overseasCr", None)

    if day1:

        feed["status"] = "success"
        feed["fetchedAt"] = day1.get(
            "fetchedAt",
            fetched_at,
        )

        feed["sourceUpdatedText"] = day1.get(
            "sourceUpdatedText"
        )

        feed["message"] = (
            "Day 1 India advance booking from "
            "BoxOfficeIndia24. Worldwide total is only "
            "available when explicitly reported with "
            "matching Day 1 scope."
        )

        if day1.get("worldwideGrossCr") is not None:
            feed["worldwideGrossCr"] = (
                day1["worldwideGrossCr"]
            )

        if day1.get("overseasGrossCr") is not None:
            feed["overseasGrossCr"] = (
                day1["overseasGrossCr"]
            )

    # -----------------------------------------------------
    # 5. SOURCE REGISTRY AND POLICY
    # -----------------------------------------------------

    configure_source_metadata(data)

    # -----------------------------------------------------
    # 6. SACNILK CONFIGURATION STATUS
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
    # 7. UPDATE STATUS
    # -----------------------------------------------------

    data["lastUpdated"] = fetched_at

    data["dataStatus"] = (
        "source_scoped_snapshot"
        if not warnings
        else "partial_source_update"
    )

    data["audienceIngestion"] = audience_ingestion

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
    # 8. WRITE DATA SAFELY
    # -----------------------------------------------------

    atomic_write_json(
        DATA_FILE,
        data,
    )

    print(
        json.dumps(
            data["ingestion"],
            ensure_ascii=False,
            indent=2,
        )
    )

    print(
        json.dumps(
            data["audienceIngestion"],
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
