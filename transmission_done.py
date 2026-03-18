#!/home/brandon/seed-reaper/.venv/bin/python3
"""
transmission_done.py

Called by Transmission on torrent completion via Settings > "Call script when
torrent is done". Transmission injects these env vars:

    TR_TORRENT_NAME   — torrent name (folder or single file)
    TR_TORRENT_DIR    — directory the torrent was saved to
    TR_TORRENT_ID     — numeric ID (used to update location via RPC after move)
    TR_TORRENT_HASH   — info hash (unused here)

The script classifies the torrent and moves it into Movies/ or Shows/ using
the same logic as organize_media.py.

Logs to ~/seed-reaper/transmission_done.log so you can see what happened
after the fact (Transmission doesn't show script output).

Usage (in Transmission settings):
    /usr/bin/python3 /home/brandon/seed-reaper/transmission_done.py
"""

import logging
import os
import sys
from pathlib import Path

# ── Reuse all the shared logic from organize_media ────────────────────────────
# Add the script's own directory to the path so the import works regardless of
# the cwd Transmission happens to use when it calls us.
sys.path.insert(0, str(Path(__file__).parent))

from transmission_rpc import Client  # noqa: E402

from env import (  # noqa: E402
    TRANSMISSION_HOST,
    TRANSMISSION_PASSWORD,
    TRANSMISSION_PORT,
    TRANSMISSION_USERNAME,
)

from organize_media import (  # noqa: E402
    MOVIES_DIR,
    SHOWS_DIR,
    SIDECAR_EXTS,
    VIDEO_EXTS,
    clean_title,
    classify_item,
    extract_season_episode,
    get_sonarr_managed_paths,
    is_sonarr_managed,
    movie_dest,
    process_movie_dir,
    process_show_dir,
    safe_move,
    show_dest,
)

# ── Logging ───────────────────────────────────────────────────────────────────

LOG_FILE = Path(__file__).parent / "transmission_done.log"

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


# ── Transmission RPC ──────────────────────────────────────────────────────────


def set_transmission_location(torrent_id: int, new_dir: Path) -> None:
    """Tell Transmission the torrent's files have moved, so it can keep seeding."""
    try:
        client = Client(
            host=TRANSMISSION_HOST,
            port=TRANSMISSION_PORT,
            username=TRANSMISSION_USERNAME,
            password=TRANSMISSION_PASSWORD,
        )
        client.move_torrent_data(torrent_id, str(new_dir), move=False)
        log("   ✓ Transmission location updated → %s", new_dir)
    except Exception as exc:
        logging.warning("   ⚠ Could not update Transmission location: %s", exc)


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    name = os.environ.get("TR_TORRENT_NAME", "").strip()
    directory = os.environ.get("TR_TORRENT_DIR", "").strip()
    torrent_id = int(os.environ.get("TR_TORRENT_ID", "0") or "0")

    if not name or not directory:
        logging.error(
            "TR_TORRENT_NAME or TR_TORRENT_DIR not set — "
            "is this being called by Transmission?"
        )
        sys.exit(1)

    item = Path(directory) / name
    if not item.exists():
        logging.error("Torrent path does not exist: %s", item)
        sys.exit(1)

    log("── New torrent: %s", name)
    log("   path: %s", item)

    # Skip hidden items
    if name.startswith("."):
        log("   SKIP (hidden)")
        return

    # Skip non-video loose files
    if item.is_file() and item.suffix.lower() not in VIDEO_EXTS | SIDECAR_EXTS:
        log("   SKIP (not a video file: %s)", item.suffix)
        return

    # Skip Sonarr-managed content
    sonarr_paths = get_sonarr_managed_paths()
    if is_sonarr_managed(item, sonarr_paths):
        log("   SKIP (Sonarr managed)")
        return

    clean, year = clean_title(name)
    kind = classify_item(name, item)
    log("   title=%r  year=%s  kind=%s", clean, year, kind)

    dry_run = False  # always act for real when called by Transmission

    if kind == "show":
        if item.is_file():
            season, _ = extract_season_episode(name)
            dest = show_dest(clean, year, season or 1, name)
            safe_move(item, dest, dry_run)
            for ext in SIDECAR_EXTS:
                sidecar = item.with_suffix(ext)
                if sidecar.exists():
                    safe_move(sidecar, dest.with_suffix(ext), dry_run)
            set_transmission_location(torrent_id, dest.parent)
        else:
            process_show_dir(item, clean, year, dry_run)
            log(
                "   ℹ Transmission location not updated (directory torrent — files were restructured)"
            )
        log("   → %s", SHOWS_DIR / clean)

    elif kind == "movie":
        if item.is_file():
            dest = movie_dest(clean, year, name)
            safe_move(item, dest, dry_run)
            for ext in SIDECAR_EXTS:
                sidecar = item.with_suffix(ext)
                if sidecar.exists():
                    safe_move(sidecar, dest.with_suffix(ext), dry_run)
            set_transmission_location(torrent_id, dest.parent)
        else:
            process_movie_dir(item, clean, year, dry_run)
            log(
                "   ℹ Transmission location not updated (directory torrent — files were restructured)"
            )
        log("   → %s", MOVIES_DIR / clean)

    else:
        log("   ❓ UNKNOWN — could not classify, leaving in place")
        log("   Manual review needed: %s", item)


if __name__ == "__main__":
    main()
