#!/usr/bin/env python3
"""
jellyfin_extras.py
Scans a Movies directory for folders containing multiple video files.
The largest file is kept as the main movie; all others are moved into
an 'Extras' subfolder so Jellyfin treats them as extras, not separate movies.
"""

import argparse
import shutil
import sys
from pathlib import Path

VIDEO_EXTENSIONS = {
    ".mkv",
    ".mp4",
    ".avi",
    ".m4v",
    ".mov",
    ".wmv",
    ".flv",
    ".ts",
    ".m2ts",
}


def find_video_files(folder: Path) -> list[Path]:
    return sorted(
        [
            f
            for f in folder.iterdir()
            if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
        ],
        key=lambda f: f.stat().st_size,
        reverse=True,
    )


def process_movies_dir(movies_dir: Path, dry_run: bool = False) -> None:
    if not movies_dir.is_dir():
        print(f"Error: '{movies_dir}' is not a directory.")
        sys.exit(1)

    moved_total = 0

    for movie_folder in sorted(movies_dir.iterdir()):
        if not movie_folder.is_dir():
            continue

        videos = find_video_files(movie_folder)

        if len(videos) <= 1:
            continue  # Nothing to do

        main = videos[0]
        extras = videos[1:]

        extras_dir = movie_folder / "Extras"

        print(f"\n{movie_folder.name}")
        print(f"  ✔ main:  {main.name}")
        for extra in extras:
            dest = extras_dir / extra.name
            if dry_run:
                print(f"  [DRY] → extras/{extra.name}")
            else:
                extras_dir.mkdir(exist_ok=True)
                shutil.move(str(extra), str(dest))
                print(f"  → extras/{extra.name}")
            moved_total += 1

    if moved_total == 0:
        print("No extra video files found — everything looks clean.")
    else:
        if dry_run:
            print(f"\nDry run: {moved_total} file(s) would be moved. Re-run without --dry-run to apply.")
        else:
            print(f"\nDone. Moved {moved_total} file(s) into Extras subfolders.")
            print("Tip: run a Jellyfin library scan to pick up the changes.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Move extra movie videos into Extras/ subfolders")
    parser.add_argument("movies_dir", nargs="?", default=str(Path.home() / "Movies"), help="Movies directory")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, move nothing")
    args = parser.parse_args()

    movies_dir = Path(args.movies_dir)
    print(f"Scanning: {movies_dir.resolve()}")
    if args.dry_run:
        print("DRY RUN — nothing will be moved")
    process_movies_dir(movies_dir, dry_run=args.dry_run)
