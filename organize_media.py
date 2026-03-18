#!/home/brandon/seed-reaper/.venv/bin/python3
"""
organize_media.py

Organises a mixed Torrents directory into separate Movies/ and Shows/ trees
suitable for Jellyfin. Queries Sonarr to skip files it manages, uses TMDB
to classify unknowns, and moves (not symlinks) everything into place.

Usage:
    python organize_media.py --dry-run        # preview only, nothing moved
    python organize_media.py                  # actually move files

Requirements:
    pip install requests
"""

import argparse
import os
import re
import shutil
import sys
import time
from pathlib import Path

import requests

from env import (
    SONARR_URL,
    SONARR_APIKEY,
    TMDB_APIKEY,
    TRANSMISSION_HOST,
    TRANSMISSION_PORT,
    TRANSMISSION_USERNAME,
    TRANSMISSION_PASSWORD,
)

# ─── CONFIG ───────────────────────────────────────────────────────────────────

SOURCE_DIR = Path("/mnt/media/Torrents")
MOVIES_DIR = Path("/mnt/media/Movies")
SHOWS_DIR = Path("/mnt/media/Shows")

DRY_RUN = False  # overridden by --dry-run flag

# Video file extensions we care about
VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".wmv"}
# Sidecar extensions that travel with a video
SIDECAR_EXTS = {".nfo", ".srt", ".ass", ".ssa", ".sub", ".idx", ".jpg", ".png", ".jpeg"}

# ─── TITLE CLEANING ───────────────────────────────────────────────────────────

# Junk patterns to strip from folder/file names before using as a title
_JUNK_PATTERNS = [
    r"www\.[^\s]+\s*-\s*",  # www.UIndex.org    -
    r"\[EZTVx?\.to\]",  # [EZTVx.to]
    r"\[TGx\]",
    r"\[y2flix[^\]]*\]",
    r"\[EtHD\]",
    r"^\s*\[[\w\s\-]+\]\s*",  # leading [GroupName] tags like [Judas], [Reaktor]
    r"\(CBB\)\s*",
    r"\.cc\s*",
    # Parenthetical release-info groups like (BD 1080p), (Dual-Audio), (HEVC-x265-10bit)
    r"\([^)]*(?:\d{3,4}p|hevc|x26[45]|blu.?ray|web.?dl|flac|aac|dts|hdr|sdr|dual.?audio)[^)]*\)",
    # Trailing "- The Complete Series" / "Complete Collection" etc.
    r"\s*[-–]\s*(?:the\s+)?complete\s+(?:series|collection|season|blu[-\s]?ray\s+box\s+set)\b.*",
]

# Release-info tokens that appear after the real title
_RELEASE_TOKENS = re.compile(
    r"[\.\s](?:"
    r"\d{3,4}p"  # 720p 1080p 2160p
    r"|(?:blu[-_]?ray|bluray|bdrip|brrip|web[-_]?dl|webrip|webdl|hdrip|hdtv|amzn|nf|cr|atvp|bili|iqiyi)"
    r"|(?:x264|x265|h\.?264|h\.?265|hevc|avc|xvid|divx)"
    r"|(?:10bit|10-bit|8bit)"
    r"|(?:aac|ac3|ddp|eac3|dts|atmos|opus|flac|mp3|dd5)"
    r"|(?:multi|dual[-_]?audio|dubbed|subbed|repack|remastered|extended|directors?.cut|dc)"
    r"|(?:season|s\d{2})"
    r"|(?:\d+ch|5\.1|7\.1|2\.0)"
    r"|(?:[a-z0-9]+-[a-z0-9]+$)"  # release group at end like FLUX, NTb, ETHEL
    r")",
    re.IGNORECASE,
)

_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_SXXEXX_RE = re.compile(r"\bS\d{2}E\d{2}\b", re.IGNORECASE)
_SXX_RE = re.compile(r"\bS(?:eason\s*)?\d{1,2}\b", re.IGNORECASE)


def clean_title(raw: str) -> tuple[str, str | None]:
    """
    Strip junk from a raw folder/file name and return (clean_title, year_or_None).
    """
    name = raw

    # Remove file extension if present
    for ext in VIDEO_EXTS | SIDECAR_EXTS:
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
            break

    # Strip leading/trailing junk patterns
    for pat in _JUNK_PATTERNS:
        name = re.sub(pat, "", name, flags=re.IGNORECASE)

    # Extract year before we nuke it
    year_match = _YEAR_RE.search(name)
    year = year_match.group(1) if year_match else None

    # Cut off at the first release-info token
    m = _RELEASE_TOKENS.search(name)
    if m:
        name = name[: m.start()]

    # Replace dots/underscores used as spaces — after the release-token cut,
    # everything remaining should be title words, so replace all dots safely.
    name = name.replace(".", " ")
    name = name.replace("_", " ")

    # Strip leftover brackets and their contents when they look like release tags
    name = re.sub(r"\[[^\]]{1,40}\]", " ", name)
    name = re.sub(r"\[[^\]]*$", " ", name)  # unclosed [ tag at end (e.g. "[BD")
    name = re.sub(r"\([^\)]{10,}\)", " ", name)  # long parenthetical junk

    # Remove season/episode markers from the title portion
    name = _SXXEXX_RE.sub("", name)
    name = _SXX_RE.sub("", name)

    # Strip the year from the title (it's returned separately for folder naming)
    # Match "(YYYY)" first so we don't leave dangling parens, then bare year
    if year:
        name = re.sub(rf"\({year}\)|\b{year}\b", "", name)

    # Normalise whitespace, strip trailing punctuation
    name = re.sub(r"\s+", " ", name).strip(" .-_")

    # Title-case
    name = name.title()

    return name, year


# ─── EPISODE DETECTION ────────────────────────────────────────────────────────


def looks_like_episode(name: str) -> bool:
    """True if the name contains SxxExx or similar episode markers."""
    return bool(_SXXEXX_RE.search(name))


def extract_season_episode(name: str) -> tuple[int | None, int | None]:
    m = re.search(r"S(\d{2})E(\d{2})", name, re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


# ─── SONARR ───────────────────────────────────────────────────────────────────


def get_sonarr_managed_paths() -> set[Path]:
    """Return the set of root-folder paths (and series paths) Sonarr manages."""
    managed = set()
    if not SONARR_APIKEY:
        print(
            "⚠  No Sonarr API key set — skipping Sonarr check (all files will be considered)"
        )
        return managed
    try:
        resp = requests.get(
            f"{SONARR_URL}/api/v3/series",
            headers={"X-Api-Key": SONARR_APIKEY},
            timeout=10,
        )
        resp.raise_for_status()
        for series in resp.json():
            path = series.get("path", "")
            if path:
                managed.add(Path(path))
        print(f"✓  Sonarr: found {len(managed)} managed series paths")
    except Exception as e:
        print(f"⚠  Could not reach Sonarr ({e}) — skipping Sonarr check")
    return managed


def is_sonarr_managed(path: Path, sonarr_paths: set[Path]) -> bool:
    """True if path is inside any Sonarr-managed series folder."""
    for sp in sonarr_paths:
        try:
            path.relative_to(sp)
            return True
        except ValueError:
            pass
    return False


# ─── TRANSMISSION ─────────────────────────────────────────────────────────────


def get_incomplete_torrent_names() -> set[str]:
    """
    Return the set of torrent names (folder/file names) that are not yet
    100 % complete in Transmission. Items in this set should be skipped.
    """
    try:
        from transmission_rpc import Client

        client = Client(
            host=TRANSMISSION_HOST,
            port=TRANSMISSION_PORT,
            username=TRANSMISSION_USERNAME,
            password=TRANSMISSION_PASSWORD,
        )
        torrents = client.get_torrents()
        incomplete = {t.name for t in torrents if t.percent_done < 1.0}
        print(
            f"✓  Transmission: {len(torrents)} torrents, {len(incomplete)} incomplete"
        )
        return incomplete
    except Exception as e:
        print(f"⚠  Could not reach Transmission ({e}) — skipping incomplete check")
        return set()


# ─── TMDB ─────────────────────────────────────────────────────────────────────

_tmdb_cache: dict[str, dict | None] = {}


def tmdb_search(title: str, year: str | None = None) -> dict | None:
    """Search TMDB for title. Returns result dict or None."""
    cache_key = f"{title}|{year}"
    if cache_key in _tmdb_cache:
        return _tmdb_cache[cache_key]

    if not TMDB_APIKEY:
        _tmdb_cache[cache_key] = None
        return None

    params = {"api_key": TMDB_APIKEY, "query": title, "include_adult": False}
    if year:
        params["year"] = year

    result = None
    for endpoint, kind in [("search/movie", "movie"), ("search/tv", "tv")]:
        try:
            r = requests.get(
                f"https://api.themoviedb.org/3/{endpoint}",
                params=params,
                timeout=10,
            )
            r.raise_for_status()
            results = r.json().get("results", [])
            if results:
                top = results[0]
                top["_kind"] = kind
                result = top
                break
        except Exception:
            pass
        time.sleep(0.25)  # be polite to TMDB

    _tmdb_cache[cache_key] = result
    return result


def classify_via_tmdb(title: str, year: str | None) -> str | None:
    """Returns 'movie', 'show', or None if TMDB can't decide."""
    # Try both endpoints and see which has a higher popularity/vote score
    best_kind = None
    best_score = -1

    if not TMDB_APIKEY:
        return None

    for endpoint, kind in [("search/movie", "movie"), ("search/tv", "tv")]:
        params = {"api_key": TMDB_APIKEY, "query": title, "include_adult": False}
        if year:
            params["year"] = year
        try:
            r = requests.get(
                f"https://api.themoviedb.org/3/{endpoint}",
                params=params,
                timeout=10,
            )
            r.raise_for_status()
            results = r.json().get("results", [])
            if results:
                score = results[0].get("popularity", 0)
                if score > best_score:
                    best_score = score
                    best_kind = kind
        except Exception:
            pass
        time.sleep(0.25)

    return best_kind  # 'movie' | 'tv' | None


# ─── HEURISTIC CLASSIFICATION ─────────────────────────────────────────────────

# Titles we know are shows (won't match cleanly on TMDB due to weird names)
_KNOWN_SHOWS = {
    "tengen toppa gurren lagann",
    "gurren lagann",
    "code geass",
    "golden kamuy",
    "arcane",
    "avatar the last airbender",
    "band of brothers",
    "better call saul",
    "blue mountain state",
    "chernobyl",
    "cyberpunk edgerunners",
    "death note",
    "euphoria",
    "fallout",
    "firefly",
    "hell on wheels",
    "jujutsu kaisen",
    "jjk",
    "lost",
    "mahou shoujo madoka magica",
    "madoka magica",
    "mindhunter",
    "odd taxi",
    "over the garden wall",
    "parasyte",
    "parasyte the maxim",
    "primal",
    "prison break",
    "rome",
    "sherlock",
    "shrinking",
    "south park",
    "spartacus house of ashur",
    "spartacus",
    "stranger things",
    "true detective",
    "violet evergarden",
    "wednesday",
    "wonder egg priority",
    "frieren",
    "sousou no frieren",
    "frieren beyond journeys end",
    "berserk",
    "fullmetal alchemist brotherhood",
    "fma brotherhood",
    "cowboy bebop",
    "blood+",
    "dorohedoro",
    "kaguya sama",
    "kaguya-sama",
    "legend of the galactic heroes",
    "a knight of the seven kingdoms",
    "the penguin",
    "hell on wheels",
    "blue planet",
    "blue planet ii",
    "the office",
    "rome",
    "prison break",
}

_KNOWN_MOVIES = {
    "american history x",
    "braveheart",
    "coco",
    "das boot",
    "das leben der anderen",
    "the lives of others",
    "django unchained",
    "drive",
    "fantastic mr fox",
    "grave of the fireflies",
    "heat",
    "ikiru",
    "memento",
    "nightcrawler",
    "oldboy",
    "once upon a time in america",
    "parasite",
    "perfect blue",
    "requiem for a dream",
    "reservoir dogs",
    "room",
    "shrek",
    "shrek 2",
    "spirited Away",
    "spirited away",
    "star wars",
    "the lord of the rings",
    "the shining",
    "the shawshank redemption",
    "the silence of the lambs",
    "the usual suspects",
    "your name",
    "kimi no na wa",
    "i want to eat your pancreas",
    "spider-man across the spider-verse",
    "spider man across the spider verse",
    "chainsaw man the movie",
    "jujutsu kaisen hidden inventory",
}


def classify_item(name: str, path: Path) -> str:
    """
    Classify a top-level item as 'movie', 'show', or 'unknown'.
    Uses: episode markers → known lists → TMDB.
    """
    # If it contains SxxExx it's definitely a show
    if looks_like_episode(name):
        return "show"

    # If it's a directory and contains episode files, it's a show
    if path.is_dir():
        for f in path.rglob("*"):
            if f.suffix.lower() in VIDEO_EXTS and looks_like_episode(f.name):
                return "show"
        # Directory of videos with no episode markers — probably a movie or season pack
        # Check for season indicators in folder name
        if _SXX_RE.search(name):
            return "show"

    clean, year = clean_title(name)
    lower = clean.lower()

    # Check known lists
    for known in _KNOWN_SHOWS:
        if known in lower:
            return "show"
    for known in _KNOWN_MOVIES:
        if known in lower:
            return "movie"

    # Fall back to TMDB
    kind = classify_via_tmdb(clean, year)
    if kind == "tv":
        return "show"
    if kind == "movie":
        return "movie"

    return "unknown"


# ─── DESTINATION PATH BUILDER ─────────────────────────────────────────────────


def show_dest(
    show_title: str, year: str | None, season: int | None, filename: str
) -> Path:
    """
    Build destination path for a show episode.
    Shows/Show Name (Year)/Season 01/filename
    """
    folder = show_title
    if year:
        folder = f"{show_title} ({year})"
    season_folder = f"Season {season:02d}" if season else "Season 01"
    dest_name = _clean_filename(filename)
    return SHOWS_DIR / folder / season_folder / dest_name


def movie_dest(movie_title: str, year: str | None, filename: str) -> Path:
    """
    Build destination path for a movie.
    Movies/Movie Name (Year)/filename
    """
    folder = movie_title
    if year:
        folder = f"{movie_title} ({year})"
    dest_name = _clean_filename(filename)
    return MOVIES_DIR / folder / dest_name


def _clean_filename(filename: str) -> str:
    """Strip torrent junk from an individual filename but keep extension."""
    p = Path(filename)
    stem, suffix = p.stem, p.suffix
    # Strip bracketed group tags at start
    stem = re.sub(r"^\[[^\]]{1,30}\]\s*", "", stem)
    # Strip www. junk
    stem = re.sub(r"www\.[^\s]+\s*-\s*", "", stem, flags=re.IGNORECASE)
    # Strip [EZTVx.to] style tags
    stem = re.sub(r"\[[^\]]{1,30}\]$", "", stem).strip()
    # Normalise dots-as-spaces but keep SxxExx intact
    # Only replace dots that are clearly word separators
    stem = re.sub(r"(?<=\w)\.(?=\w)(?![A-Z]{2,})", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" .-")
    return stem + suffix


# ─── MOVE LOGIC ───────────────────────────────────────────────────────────────


def safe_move(src: Path, dest: Path, dry_run: bool) -> bool:
    if dest.exists():
        print(f"    ⚠  SKIP (already exists): {dest}")
        return False
    if dry_run:
        print(f"    [DRY] {src} → {dest}")
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(src), str(dest))
    except PermissionError:
        # File may be read-only (common with torrent clients); chmod then retry
        try:
            os.chmod(str(src), 0o644)
            shutil.move(str(src), str(dest))
        except Exception as e:
            print(f"    ✗  FAILED ({e}): {src}")
            return False
    return True


def process_show_dir(src_dir: Path, show_title: str, year: str | None, dry_run: bool):
    """Move all video+sidecar files from a show directory into the right season folders."""
    for f in sorted(src_dir.rglob("*")):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext not in VIDEO_EXTS | SIDECAR_EXTS:
            continue
        season, _ = extract_season_episode(f.name)
        if season is None:
            # Try to get season from parent folder name
            season_m = re.search(r"[Ss](?:eason\s*)?(\d{1,2})", f.parent.name)
            season = int(season_m.group(1)) if season_m else 1
        dest = show_dest(show_title, year, season, f.name)
        safe_move(f, dest, dry_run)


def process_movie_dir(src_dir: Path, movie_title: str, year: str | None, dry_run: bool):
    """Move video+sidecar files from a movie directory."""
    for f in sorted(src_dir.rglob("*")):
        if not f.is_file():
            continue
        if f.suffix.lower() not in VIDEO_EXTS | SIDECAR_EXTS:
            continue
        dest = movie_dest(movie_title, year, f.name)
        safe_move(f, dest, dry_run)


# ─── MAIN ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Organise mixed media into Movies/ and Shows/"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview only, move nothing"
    )
    parser.add_argument("--source", default=str(SOURCE_DIR), help="Source directory")
    parser.add_argument(
        "--movies", default=str(MOVIES_DIR), help="Movies output directory"
    )
    parser.add_argument(
        "--shows", default=str(SHOWS_DIR), help="Shows output directory"
    )
    args = parser.parse_args()

    source = Path(args.source)
    dry_run = args.dry_run

    if dry_run:
        print("=" * 60)
        print("DRY RUN — nothing will be moved")
        print("=" * 60)

    if not source.exists():
        print(f"ERROR: source dir does not exist: {source}")
        sys.exit(1)

    # Get Sonarr managed paths so we don't touch them
    sonarr_paths = get_sonarr_managed_paths()

    # Get names of torrents still downloading in Transmission
    incomplete_torrents = get_incomplete_torrent_names()

    unknowns = []

    # Iterate top-level items in source
    items = sorted(source.iterdir())
    for item in items:
        name = item.name

        # Skip hidden files
        if name.startswith("."):
            continue

        # Skip loose non-video files at root
        if item.is_file() and item.suffix.lower() not in VIDEO_EXTS | SIDECAR_EXTS:
            continue

        # Skip if Sonarr manages this
        if is_sonarr_managed(item, sonarr_paths):
            print(f"⏭  SONARR MANAGED, skipping: {name}")
            continue

        # Skip if an incomplete Transmission torrent exists for this item
        if name in incomplete_torrents:
            print(f"⏭  INCOMPLETE TORRENT, skipping: {name}")
            continue

        print(f"\n── {name}")

        kind = classify_item(name, item)
        clean, year = clean_title(name)

        print(f"   title={clean!r}  year={year}  kind={kind}")

        if kind == "show":
            if item.is_file():
                # Loose episode file at root
                season, _ = extract_season_episode(name)
                dest = show_dest(clean, year, season or 1, name)
                safe_move(item, dest, dry_run)
                # Move sidecar with same stem
                for ext in SIDECAR_EXTS:
                    sidecar = item.with_suffix(ext)
                    if sidecar.exists():
                        safe_move(sidecar, dest.with_suffix(ext), dry_run)
            else:
                process_show_dir(item, clean, year, dry_run)

        elif kind == "movie":
            if item.is_file():
                dest = movie_dest(clean, year, name)
                safe_move(item, dest, dry_run)
                for ext in SIDECAR_EXTS:
                    sidecar = item.with_suffix(ext)
                    if sidecar.exists():
                        safe_move(sidecar, dest.with_suffix(ext), dry_run)
            else:
                process_movie_dir(item, clean, year, dry_run)

        else:
            print("   ❓ UNKNOWN — will need manual review")
            unknowns.append((name, clean, year))

    # Summary
    print("\n" + "=" * 60)
    if unknowns:
        print(f"⚠  {len(unknowns)} items could not be classified automatically:")
        for raw, clean, year in unknowns:
            print(f"   - {raw!r}  (parsed as {clean!r}, {year})")
    else:
        print("✓  All items classified")

    if dry_run:
        print("\nRe-run without --dry-run to actually move files.")


if __name__ == "__main__":
    main()
