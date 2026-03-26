#!/home/brandon/seed-reaper/.venv/bin/python3
"""
seed_reaper.py
Automatically deletes torrents from Transmission if they're
too old or they've sufficiently seeded enough already.
"""

import logging
import sys
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from transmission_rpc import Client
from env import (
    MAX_AGE_DAYS,
    MAX_RATIO,
    TRANSMISSION_PORT,
    TRANSMISSION_HOST,
    TRANSMISSION_PASSWORD,
    TRANSMISSION_USERNAME,
)

LOG_FILE = Path(__file__).parent / "seed_reaper.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.info


def main() -> None:
    client = Client(
        host=TRANSMISSION_HOST,
        username=TRANSMISSION_USERNAME,
        password=TRANSMISSION_PASSWORD,
        port=TRANSMISSION_PORT,
    )

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=MAX_AGE_DAYS)
    torrents = client.get_torrents()

    for torrent in torrents:
        log("Processing [%s]", torrent.name)

        if torrent.status != "seeding":
            log("Not seeding yet.")
            continue

        is_old = torrent.done_date and torrent.done_date < cutoff
        ratio_met = torrent.ratio >= MAX_RATIO

        if ratio_met or is_old:
            log("Removing with done_date=%s,ratio=%s", torrent.done_date, torrent.ratio)
            client.remove_torrent(torrent.id, delete_data=False)
        else:
            log("Skipping because done_date=%s,ratio=%s", torrent.done_date, torrent.ratio)


if __name__ == "__main__":
    main()
