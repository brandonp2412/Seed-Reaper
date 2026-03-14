from datetime import datetime, timedelta, timezone

from transmission_rpc import Client
from env import (
    MAX_AGE_DAYS,
    MAX_RATIO,
    TRANSMISSION_PORT,
    TRANSMISSION_HOST,
    TRANSMISSION_PASSWORD,
    TRANSMISSION_USERNAME,
)

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
    print(f"Processing [{torrent.name}]")

    if torrent.status != "seeding":
        print("Not seeding yet.")
        continue

    is_old = torrent.done_date and torrent.done_date < cutoff
    ratio_met = torrent.ratio >= MAX_RATIO

    if ratio_met or is_old:
        print(f"Removing with done_date={torrent.done_date},ratio={torrent.ratio}")
        client.remove_torrent(torrent.id, delete_data=False)
    else:
        print(f"Skipping because done_date={torrent.done_date},ratio={torrent.ratio}")
