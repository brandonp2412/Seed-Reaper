![Logo](logo.png)

# Seed Reaper

A collection of Python scripts for automating a home media server stack — Transmission + Jellyfin + Sonarr. Download a torrent, walk away, and come back to a fully organized library that's still seeding.

---

## Overview

```
Transmission finishes a download
        │
        ▼
transmission_done.py  ──►  organize_media.py logic
        │                   (classify → clean title → move)
        │
        ▼
  Files land in Movies/ or Shows/  (Jellyfin-ready structure)
        │
        ▼
Transmission keeps seeding from the new location
        │
        ▼
seed_reaper.py  (run periodically)
        │
        ├── ratio ≥ MAX_RATIO?  ──► remove torrent (keep files)
        └── age   ≥ MAX_AGE_DAYS? ──► remove torrent (keep files)
```

---

## Scripts

### `seed_reaper.py` — Torrent reaper

Connects to Transmission and removes any seeding torrent that has either:

- reached the target upload **ratio** (`MAX_RATIO`, default `2.0`), or
- been seeding longer than **`MAX_AGE_DAYS`** (default `30` days)

Files are **never deleted** — only the torrent entry is removed from Transmission.

```bash
python seed_reaper.py
```

Run this on a schedule (e.g. daily via cron) to keep your client uncluttered.

---

### `organize_media.py` — Bulk media organizer

Scans a source `Torrents/` directory and moves everything into a clean Jellyfin-compatible tree:

```
Movies/
  Movie Title (2023)/
    Movie.Title.2023.mkv

Shows/
  Show Name (2019)/
    Season 01/
      Show.Name.S01E01.mkv
```

**Classification pipeline** (in order):

1. **Episode markers** — `S01E01` in the filename → show
2. **Directory scan** — contains episode files → show; contains `S##` in name → show
3. **Known lists** — hardcoded sets of known movies and shows for unambiguous titles
4. **TMDB** — searches both movie and TV endpoints; highest popularity score wins
5. Falls back to `unknown` and prints a manual-review list

**Sonarr integration** — queries your Sonarr instance and skips anything it already manages, so the two systems never conflict.

**Title cleaning** — strips release-group tags (`[FLUX]`, `[EZTVx.to]`), codec tokens (`x265`, `BluRay`, `1080p`), normalises dots-as-spaces, and title-cases the result.

```bash
# Preview without moving anything
python organize_media.py --dry-run

# Move for real
python organize_media.py

# Custom paths
python organize_media.py --source /mnt/media/Torrents --movies /mnt/media/Movies --shows /mnt/media/Shows
```

---

### `transmission_done.py` — On-completion hook

Called automatically by Transmission when a torrent finishes downloading (configured in **Settings → Downloading → Call script when torrent is done**).

Transmission injects these environment variables:

| Variable | Description |
|---|---|
| `TR_TORRENT_NAME` | Torrent name (folder or single file) |
| `TR_TORRENT_DIR` | Directory the torrent was saved to |
| `TR_TORRENT_ID` | Numeric ID used to update the location via RPC |
| `TR_TORRENT_HASH` | Info hash (unused) |

The script reuses all the logic from `organize_media.py` to classify and move the file, then calls `move_torrent_data` via the Transmission RPC so the client knows the new location and **continues seeding without interruption**.

Logs everything to `transmission_done.log` in the project directory (since Transmission swallows stdout).

**Setup in Transmission:**
```
/usr/bin/python3 /home/<user>/seed-reaper/transmission_done.py
```

---

### `jellyfin_extras.py` — Extras organizer

Some torrents (director's cuts, bonus reels, trailers) land alongside the main movie file in the same folder. Jellyfin sees them as separate movies. This script fixes that.

For every movie folder containing **more than one video file**, it:

1. Keeps the **largest** file as the main movie
2. Moves all smaller files into an `Extras/` subfolder

```bash
# Default: ~/Movies
python jellyfin_extras.py

# Custom path
python jellyfin_extras.py /mnt/media/Movies
```

Run a Jellyfin library scan afterward to pick up the changes.

---

## Configuration

```bash
cp env.example.py env.py
# then edit env.py with your values
```

`env.py` is gitignored — never commit it. `env.example.py` is the template that lives in the repo:

| Variable | Description | Default |
|---|---|---|
| `TRANSMISSION_HOST` | IP/hostname of your Transmission instance | — |
| `TRANSMISSION_PORT` | Transmission RPC port | `9091` |
| `TRANSMISSION_USERNAME` | Transmission RPC username | — |
| `TRANSMISSION_PASSWORD` | Transmission RPC password | — |
| `SONARR_APIKEY` | Sonarr API key — set `""` to disable | `""` |
| `TMDB_APIKEY` | TMDB API key — set `""` to disable | `""` |
| `MAX_RATIO` | Remove torrent when upload ratio reaches this | `2.0` |
| `MAX_AGE_DAYS` | Remove torrent after seeding this many days | `30` |

---

## Installation

```bash
# Clone to your media server
git clone <repo-url> ~/seed-reaper
cd ~/seed-reaper

# Install dependencies
pip install -r requirements.txt
```

Or deploy from your local machine to the server with the included helper:

```bash
bash deploy.sh   # SCPs everything to jellyfin:~/seed-reaper and pip-installs
```

### Dependencies

- [`transmission-rpc`](https://github.com/Trim21/transmission-rpc) — Transmission JSON-RPC client
- [`requests`](https://docs.python-requests.org/) — HTTP calls to Sonarr and TMDB

---

## Recommended cron setup

```cron
# Run seed_reaper every day at 3 AM
0 3 * * * /usr/bin/python3 /home/<user>/seed-reaper/seed_reaper.py >> /home/<user>/seed-reaper/reaper.log 2>&1
```

---

## Media directory layout (expected by Jellyfin)

```
/mnt/media/
├── Torrents/          ← Transmission download directory
├── Movies/
│   ├── Dune (2021)/
│   │   ├── Dune.2021.mkv
│   │   └── Extras/
│   │       └── Behind.the.Scenes.mkv
│   └── Oppenheimer (2023)/
│       └── Oppenheimer.2023.mkv
└── Shows/
    ├── Breaking Bad (2008)/
    │   ├── Season 01/
    │   │   ├── Breaking.Bad.S01E01.mkv
    │   │   └── Breaking.Bad.S01E02.mkv
    │   └── Season 02/
    └── Fallout (2024)/
        └── Season 01/
```
