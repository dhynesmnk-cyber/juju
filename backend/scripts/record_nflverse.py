"""Record a slice of nflverse's release files for tests: the rows for a few games, unchanged.

    python scripts/record_nflverse.py 2026 401872963 401872964

Downloads the schedule, players, weekly player stats and play-by-play for the season, and writes
the rows for those ESPN event ids to tests/fixtures/nflverse/ as plain CSV with every column.
Players are the ones with a stat line in those games. nflverse is free: this costs nothing, but
it uses the network, so it is a script and never part of the tests.
"""
import csv
import gzip
import io
import sys
from pathlib import Path

import httpx

BASE_URL = "https://github.com/nflverse/nflverse-data/releases/download"
OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "nflverse"


def _rows(url: str) -> tuple[list[str], list[dict[str, str]]]:
    raw = httpx.get(url, follow_redirects=True, timeout=120,
                    headers={"User-Agent": "Juju/1.0 (fixture recorder)"}).raise_for_status()
    data = raw.content
    text = gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data
    reader = csv.DictReader(io.StringIO(text.decode("utf-8"), newline=""))
    return list(reader.fieldnames or ()), list(reader)


def _write(name: str, fields: list[str], rows: list[dict[str, str]]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / name).open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"{name}: {len(rows)} rows")


def main(season: int, espn_ids: list[str]) -> None:
    fields, games = _rows(f"{BASE_URL}/schedules/games.csv.gz")
    games = [g for g in games if g["espn"] in espn_ids]
    _write("games.csv", fields, games)
    game_ids = {g["game_id"] for g in games}

    fields, stats = _rows(f"{BASE_URL}/stats_player/stats_player_week_{season}.csv.gz")
    stats = [r for r in stats if r["game_id"] in game_ids]
    _write(f"stats_player_week_{season}.csv", fields, stats)

    fields, players = _rows(f"{BASE_URL}/players/players.csv.gz")
    gsis = {r["player_id"] for r in stats}
    _write("players.csv", fields, [p for p in players if p["gsis_id"] in gsis])

    fields, pbp = _rows(f"{BASE_URL}/pbp/play_by_play_{season}.csv.gz")
    _write(f"play_by_play_{season}.csv", fields, [p for p in pbp if p["game_id"] in game_ids])


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(int(sys.argv[1]), sys.argv[2:])
