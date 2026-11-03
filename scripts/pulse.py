#!/usr/bin/env python3
"""
ReelMetrics — Audience Pulse
Free, evidence-led topic and sentiment analysis for TMDB written reviews.

YouTube Data API is used only to discover relevant review videos. The API does
not provide spoken transcripts, so video metadata is never used as sentiment
evidence. No generative AI API is required.
"""

import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "reviews.json"

MOVIE_TITLE = "Ramayana: Rise of a Legend"
TMDB_API = "https://api.themoviedb.org/3"
YOUTUBE_API = "https://www.googleapis.com/youtube/v3"

ANALYZER = SentimentIntensityAnalyzer()
MAX_REVIEWS = 60
MIN_REVIEW_LENGTH = 100
MIN_INDEPENDENT_REVIEWS = 2

# Topic terms are matched against sentences, not whole-review sentiment.
TOPICS = {
    "Story & screenplay": [
        "story", "screenplay", "writing", "plot", "narrative",
        "character", "dialogue", "pacing"
    ],
    "Performances": [
        "acting", "performance", "actor", "actress", "cast",
        "portrayal", "chemistry"
    ],
    "Visual experience": [
        "visual", "cinematography", "vfx", "effects",
        "animation", "cgi", "imagery"
    ],
    "Music & sound": [
        "music", "soundtrack", "score", "background music",
        "songs", "sound design", "audio"
    ],
    "Action & scale": [
        "action", "battle", "war", "scale", "spectacle",
        "choreography", "stunts"
    ],
}

# A Mixed highlight is eligible only when the critical observations include
# constructive language. This is a conservative heuristic, not moderation.
CONSTRUCTIVE_TERMS = (
    "could", "would", "should", "needs", "need more", "needed",
    "could have", "would have", "better if", "improve", "improved",
    "improvement", "more development", "underdeveloped", "too long",
    "too short", "felt rushed", "pacing", "lacked", "less"
)

def get_json(url, token=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc

def find_movie(token):
    params = urlencode({
        "query": MOVIE_TITLE,
        "include_adult": "false",
        "language": "en-US",
    })
    data = get_json(f"{TMDB_API}/search/movie?{params}", token)
    normalize = lambda value: re.sub(r"[^a-z0-9]", "", value.lower())
    matches = [
        movie for movie in data.get("results", [])
        if normalize(movie.get("title") or "") == normalize(MOVIE_TITLE)
    ]
    if not matches:
        raise RuntimeError(
            "No exact TMDB title match. Set TMDB_MOVIE_ID as a repository "
            "variable after verifying the correct film entry."
        )
    matches.sort(
        key=lambda movie: (
            movie.get("release_date") or "",
            movie.get("popularity") or 0
        ),
        reverse=True,
    )
    return matches[0]

def fetch_reviews(movie_id, token):
    reviews = []
    for page in range(1, 4):
        params = urlencode({"language": "en-US", "page": page})
        data = get_json(
            f"{TMDB_API}/movie/{movie_id}/reviews?{params}", token
        )
        for item in data.get("results", []):
            content = re.sub(r"\s+", " ", item.get("content") or "").strip()
            if len(content) < MIN_REVIEW_LENGTH:
                continue
            reviews.append({
                "id": len(reviews) + 1,
                "text": content[:6000],
                "url": item.get("url") or "",
                "author": item.get("author") or "TMDB reviewer",
                "created_at": item.get("created_at") or "",
            })
            if len(reviews) >= MAX_REVIEWS:
                return reviews
        if page >= int(data.get("total_pages") or 1):
            break
    return reviews

def discover_youtube():
    """Discover relevant review videos; never infer their spoken content."""
    key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not key:
        return []
    videos = {}
    queries = [
        f'"{MOVIE_TITLE}" movie review',
        f'"{MOVIE_TITLE}" audience review',
    ]
    for query in queries:
        params = urlencode({
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": 5,
            "order": "date",
            "key": key,
        })
        data = get_json(f"{YOUTUBE_API}/search?{params}")
        for item in data.get("items", []):
            video_id = (item.get("id") or {}).get("videoId")
            if not video_id:
                continue
            snippet = item.get("snippet") or {}
            videos[video_id] = {
                "title": (snippet.get("title") or "")[:180],
                "publishedAt": snippet.get("publishedAt") or "",
                "url": f"https://www.youtube.com/watch?v={video_id}",
            }
    return list(videos.values())

def split_sentences(text):
    # Lightweight sentence segmentation; avoids extra NLP model downloads.
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", text)
        if len(sentence.strip()) >= 12
    ]

def topic_sentences(review_text, keywords):
    matches = []
    for sentence in split_sentences(review_text):
        lower = sentence.lower()
        if any(re.search(r"\b" + re.escape(term) + r"\b", lower)
               for term in keywords):
            score = ANALYZER.polarity_scores(sentence)["compound"]
            matches.append((sentence, score))
    return matches

def analyse_topics(reviews):
    by_topic = defaultdict(list)
    for review in reviews:
        for topic, keywords in TOPICS.items():
            sentences = topic_sentences(review["text"], keywords)
            if not sentences:
                continue
            positive = [s for s, score in sentences if score >= 0.05]
            critical = [s for s, score in sentences if score <= -0.05]
            neutral = [s for s, score in sentences if -0.05 < score < 0.05]
            by_topic[topic].append({
                "review_id": review["id"],
                "url": review["url"],
                "positive": positive,
                "critical": critical,
                "neutral": neutral,
            })
    return by_topic

def build_highlights(reviews):
    topic_data = analyse_topics(reviews)
    highlights = []

    for topic, observations in topic_data.items():
        if len({item["review_id"] for item in observations}) < MIN_INDEPENDENT_REVIEWS:
            continue

        positive_reviews = [
            item for item in observations if item["positive"]
        ]
        critical_reviews = [
            item for item in observations if item["critical"]
        ]

        # Positive: at least two independent reviews contain positive
        # topic-specific language, and positive evidence is more prevalent.
        if len(positive_reviews) >= MIN_INDEPENDENT_REVIEWS and (
            len(positive_reviews) > len(critical_reviews)
        ):
            sentiment = "Positive"
            summary = (
                f"Topic-specific comments on {topic.lower()} lean positive "
                f"in this sample: {len(positive_reviews)} of "
                f"{len(observations)} reviews contain positive observations. "
                "This describes the collected TMDB review sample, not all viewers."
            )

        # Mixed is allowed only if both positive and critical observations
        # appear, and at least one critical observation contains constructive
        # wording. Critical-only themes are excluded.
        elif positive_reviews and critical_reviews:
            constructive = [
                sentence
                for item in observations
                for sentence in item["critical"]
                if any(term in sentence.lower() for term in CONSTRUCTIVE_TERMS)
            ]
            if not constructive:
                continue
            sentiment = "Mixed"
            summary = (
                f"Feedback on {topic.lower()} is mixed in this sample: "
                f"{len(positive_reviews)} reviews contain positive observations, "
                f"while {len(critical_reviews)} include criticism. "
                "Some criticism is phrased as a specific improvement or execution suggestion."
            )
        else:
            # Exclude critical-only and neutral-only topics.
            continue

        evidence = []
        for item in observations:
            if item["positive"] or item["critical"]:
                evidence.append({
                    "source": "TMDB",
                    "review_id": item["review_id"],
                    "url": item["url"],
                })

        highlights.append({
            "category": topic,
            "sentiment": sentiment,
            "text": summary,
            "evidence": evidence,
        })

    highlights.sort(
        key=lambda item: (
            len(item["evidence"]),
            item["sentiment"] == "Positive",
        ),
        reverse=True,
    )
    return highlights[:4]

def main():
    previous = {}
    if OUTPUT.exists():
        try:
            previous = json.loads(OUTPUT.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            previous = {}

    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    try:
        token = os.getenv("TMDB_READ_ACCESS_TOKEN", "").strip()
        if not token:
            raise RuntimeError("Missing TMDB_READ_ACCESS_TOKEN GitHub secret.")

        movie_id_override = os.getenv("TMDB_MOVIE_ID", "").strip()
        movie = (
            {"id": int(movie_id_override), "title": MOVIE_TITLE}
            if movie_id_override
            else find_movie(token)
        )

        reviews = fetch_reviews(movie["id"], token)
        if len(reviews) < MIN_INDEPENDENT_REVIEWS:
            raise RuntimeError(
                f"Only {len(reviews)} substantial TMDB reviews found."
            )

        highlights = build_highlights(reviews)
        if not highlights:
            raise RuntimeError(
                "No Positive or constructive Mixed themes met the evidence rules. "
                "Existing published highlights will be retained."
            )

        youtube_videos = discover_youtube()

        output = {
            "movie": MOVIE_TITLE,
            "status": "success",
            "updatedAt": now,
            "highlights": highlights,
            "editorialNote": (
                "Original, rule-based summaries of TMDB written reviews. "
                "Only Positive and constructive Mixed themes are published."
            ),
            "internalAudit": {
                "tmdbMovieId": movie["id"],
                "tmdbReviewCount": len(reviews),
                "youtubeVideosDiscovered": youtube_videos,
                "youtubeUsedAsSentimentEvidence": False,
                "sentimentEngine": "VADER",
                "publishedHighlightCount": len(highlights),
            },
        }
        OUTPUT.write_text(
            json.dumps(output, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"Published {len(highlights)} evidence-backed highlights "
            f"from {len(reviews)} TMDB reviews."
        )

    except Exception as error:
        previous["movie"] = MOVIE_TITLE
        previous["status"] = (
            "stale" if previous.get("highlights") else "insufficient_data"
        )
        previous["lastAttemptAt"] = now
        previous["lastError"] = str(error)
        previous.setdefault("highlights", [])
        OUTPUT.write_text(
            json.dumps(previous, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Audience Pulse not replaced: {error}", file=sys.stderr)
        raise

if __name__ == "__main__":
    main()
