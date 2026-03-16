#!/usr/bin/env python3
"""
anime_picker.py

Picks a random top anime from MyAnimeList (via Jikan API) and adds it to Sonarr.

Run weekly via cron:
    0 10 * * 1  /usr/bin/python3 /home/brandon/seed-reaper/anime_picker.py

Logs to ~/seed-reaper/anime_picker.log
"""

import json
import logging
import random
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from env import SONARR_URL, SONARR_APIKEY

# ── Config ────────────────────────────────────────────────────────────────────

JIKAN_URL       = "https://api.jikan.moe/v4"

# How many of the top anime to sample from
TOP_N           = 50

# Minimum MAL score to consider (0.0 to skip filter)
MIN_SCORE       = 7.5

# Sonarr settings for new series
QUALITY_PROFILE = 4          # HD-1080p
ROOT_FOLDER     = "/var/lib/sonarr"
SERIES_TYPE     = "anime"    # proper anime episode ordering
MONITORED       = True
SEARCH_ON_ADD   = True

# MAL genre IDs to exclude
EXCLUDED_GENRES = {12}       # 12 = Hentai

LOG_FILE = Path(__file__).parent / "anime_picker.log"

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.info


# ── Jikan (MAL) ───────────────────────────────────────────────────────────────

def get_top_anime(limit: int = TOP_N) -> list[dict]:
    """Fetch top anime from MAL via Jikan, filtering by score and genre."""
    all_anime = []
    page = 1
    per_page = 25

    while len(all_anime) < limit:
        try:
            r = requests.get(
                f"{JIKAN_URL}/top/anime",
                params={
                    "type": "tv",
                    "filter": "bypopularity",
                    "page": page,
                    "limit": per_page,
                    "sfw": "true",
                },
                timeout=15,
            )
            r.raise_for_status()
        except requests.RequestException as e:
            logging.error("Jikan request failed: %s", e)
            break

        data = r.json().get("data", [])
        if not data:
            break

        for anime in data:
            # Skip excluded genres
            genre_ids = {g["mal_id"] for g in anime.get("genres", [])}
            if genre_ids & EXCLUDED_GENRES:
                continue
            # Skip below minimum score
            score = anime.get("score") or 0.0
            if MIN_SCORE and score < MIN_SCORE:
                continue
            all_anime.append(anime)
            if len(all_anime) >= limit:
                break

        page += 1
        time.sleep(0.4)  # Jikan rate limit: 3 req/sec

    return all_anime


# ── Sonarr ────────────────────────────────────────────────────────────────────

_SONARR_HEADERS = {"X-Api-Key": SONARR_APIKEY}


def get_existing_tvdb_ids() -> set[int]:
    """Return set of tvdbIds already in Sonarr."""
    try:
        r = requests.get(
            f"{SONARR_URL}/api/v3/series",
            headers=_SONARR_HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        return {s["tvdbId"] for s in r.json() if s.get("tvdbId")}
    except requests.RequestException as e:
        logging.error("Could not reach Sonarr: %s", e)
        return set()


def sonarr_lookup(title: str) -> dict | None:
    """Look up a series by title in Sonarr (searches TVDB). Returns first match."""
    try:
        r = requests.get(
            f"{SONARR_URL}/api/v3/series/lookup",
            params={"term": title},
            headers=_SONARR_HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        results = r.json()
        return results[0] if results else None
    except requests.RequestException as e:
        logging.error("Sonarr lookup failed for %r: %s", title, e)
        return None


def add_to_sonarr(series: dict) -> bool:
    """POST a series to Sonarr. `series` is a result from sonarr_lookup."""
    payload = {
        **series,
        "qualityProfileId": QUALITY_PROFILE,
        "rootFolderPath": ROOT_FOLDER,
        "seriesType": SERIES_TYPE,
        "monitored": MONITORED,
        "addOptions": {
            "monitor": "all",
            "searchForMissingEpisodes": SEARCH_ON_ADD,
            "searchForCutoffUnmetEpisodes": False,
        },
    }
    try:
        r = requests.post(
            f"{SONARR_URL}/api/v3/series",
            json=payload,
            headers=_SONARR_HEADERS,
            timeout=15,
        )
        if r.status_code == 201:
            return True
        if r.status_code == 400:
            body = r.json()
            if any("already exists" in str(e) for e in body):
                logging.info("   already in Sonarr (race condition)")
                return True
        logging.error("Sonarr add failed (%s): %s", r.status_code, r.text[:300])
        return False
    except requests.RequestException as e:
        logging.error("Sonarr add request failed: %s", e)
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    log("── anime_picker starting")

    existing = get_existing_tvdb_ids()
    log("   Sonarr has %d series", len(existing))

    candidates = get_top_anime(TOP_N)
    log("   Jikan returned %d candidates (score ≥ %.1f)", len(candidates), MIN_SCORE)

    if not candidates:
        logging.error("No candidates from Jikan — aborting")
        sys.exit(1)

    # Shuffle and work through candidates until we add one that isn't already present
    random.shuffle(candidates)

    for anime in candidates:
        title    = anime["title"]
        mal_id   = anime["mal_id"]
        score    = anime.get("score", "?")
        episodes = anime.get("episodes", "?")
        genres   = ", ".join(g["name"] for g in anime.get("genres", []))

        log("   Trying: %s (MAL #%s, score=%s, eps=%s, genres=%s)",
            title, mal_id, score, episodes, genres)

        series = sonarr_lookup(title)
        if not series:
            log("   SKIP — not found in TVDB via Sonarr")
            continue

        tvdb_id = series.get("tvdbId")
        if tvdb_id in existing:
            log("   SKIP — already in Sonarr (tvdbId=%s)", tvdb_id)
            continue

        log("   Adding to Sonarr: %r (tvdbId=%s)", series["title"], tvdb_id)
        ok = add_to_sonarr(series)
        if ok:
            log("   ✓ Added! Sonarr will search for episodes now.")
            return

    logging.error("Exhausted all %d candidates without adding anything", len(candidates))
    sys.exit(1)


if __name__ == "__main__":
    main()
