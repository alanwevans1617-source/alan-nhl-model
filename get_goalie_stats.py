#!/usr/bin/env python3
"""
Download this season's goalie stats from MoneyPuck and save goalie_stats.csv for V3.
Data: MoneyPuck.com (free for non-commercial use; credit them).

  py get_goalie_stats.py

Uses THIS SEASON ONLY. For each goalie:
  GSAx = expected goals against - actual goals against (positive = saving more than
  an average goalie would on the same shots).
  The rating is shrunk toward league average until a goalie has faced a lot of shots:
      rating per shot = GSAx / (shots + K)        K = 1000 shots by default
  so a goalie with 300 shots keeps about 23% of his GSAx rate, one with 1000 keeps 50%.
  A goalie with no games this season counts as exactly league average.

goals_saved_per_game = rating per shot x league shots on goal per 60 minutes.
Downloads once per day; running it again the same day reuses the file.
"""
import argparse
import io
import os
import re
import sys
import unicodedata
from datetime import date, datetime

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "goalie_stats.csv")
RAW = os.path.join(HERE, "moneypuck_goalies.csv")
URL = "https://moneypuck.com/moneypuck/playerData/seasonSummary/{}/regular/goalies.csv"

# MoneyPuck team codes -> Hockey-Reference names (same as get_xg.py)
TEAMS = {
    "ANA": "Anaheim Ducks", "BOS": "Boston Bruins", "BUF": "Buffalo Sabres",
    "CAR": "Carolina Hurricanes", "CBJ": "Columbus Blue Jackets", "CGY": "Calgary Flames",
    "CHI": "Chicago Blackhawks", "COL": "Colorado Avalanche", "DAL": "Dallas Stars",
    "DET": "Detroit Red Wings", "EDM": "Edmonton Oilers", "FLA": "Florida Panthers",
    "LAK": "Los Angeles Kings", "L.A": "Los Angeles Kings", "MIN": "Minnesota Wild",
    "MTL": "Montreal Canadiens", "NJD": "New Jersey Devils", "N.J": "New Jersey Devils",
    "NSH": "Nashville Predators", "NYI": "New York Islanders", "NYR": "New York Rangers",
    "OTT": "Ottawa Senators", "PHI": "Philadelphia Flyers", "PIT": "Pittsburgh Penguins",
    "SEA": "Seattle Kraken", "SJS": "San Jose Sharks", "S.J": "San Jose Sharks",
    "STL": "St. Louis Blues", "TBL": "Tampa Bay Lightning", "T.B": "Tampa Bay Lightning",
    "TOR": "Toronto Maple Leafs", "UTA": "Utah Mammoth", "UTAH": "Utah Mammoth",
    "VAN": "Vancouver Canucks", "VGK": "Vegas Golden Knights", "WPG": "Winnipeg Jets",
    "WSH": "Washington Capitals",
}


def key(name):
    """Name -> matching key: no accents, case, spaces or punctuation."""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s.lower())


def season_start(today):
    return today.year if today.month >= 8 else today.year - 1  # 2026 = the 2026-27 season


def download(season):
    fresh = (os.path.exists(RAW) and
             datetime.fromtimestamp(os.path.getmtime(RAW)).date() == date.today())
    if fresh:
        print("Using today's MoneyPuck goalie download (already fetched today).")
        return pd.read_csv(RAW)
    url = URL.format(season)
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    if r.status_code != 200:
        sys.exit(f"MoneyPuck returned error {r.status_code} for {url}\n"
                 "Open moneypuck.com/data.htm in a browser to check, then paste this message into chat.")
    with open(RAW, "w", encoding="utf-8", newline="") as f:
        f.write(r.text)
    return pd.read_csv(io.StringIO(r.text))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=float, default=1000, help="shrinkage in shots (default 1000)")
    args = ap.parse_args()
    season = season_start(date.today())
    df = download(season)

    need = ["name", "team", "situation", "xGoals", "goals", "icetime"]
    missing = [c for c in need if c not in df.columns]
    shots_col = next((c for c in ("ongoal", "shotsOnGoal", "shots_on_goal") if c in df.columns), None)
    if missing or not shots_col:
        sys.exit(f"MoneyPuck's goalie file looks different than expected. Missing: "
                 f"{missing + ([] if shots_col else ['ongoal'])}\nColumns found: {list(df.columns)[:40]}\n"
                 "Paste this message into chat.")
    if "season" in df.columns and not (df.season == season).any():
        sys.exit(f"No {season} rows in MoneyPuck's goalie file. Paste this message into chat.")

    d = df[df.situation == "all"].copy()
    d["shots"] = pd.to_numeric(d[shots_col], errors="coerce").fillna(0)
    d["gsax"] = d.xGoals - d.goals
    d["team_name"] = d.team.map(TEAMS)
    unknown = sorted(set(d.loc[d.team_name.isna(), "team"]))
    if unknown:
        print(f"Warning: unknown team codes (goalies kept, team unknown): {unknown}")

    shots_per_60 = d.shots.sum() / (d.icetime.sum() / 3600)
    d["rating_per_shot"] = d.gsax / (d.shots + args.k)
    d["saved_per_game"] = d.rating_per_shot * shots_per_60
    d["raw_sv_pct"] = 1 - d.goals / d.shots.where(d.shots > 0)
    d["games"] = d.get("games_played", pd.Series(index=d.index, dtype=float))
    out = pd.DataFrame({
        "name": d.name, "key": d.name.map(key), "team": d.team_name.fillna(d.team),
        "games": d.games, "shots": d.shots.astype(int), "goals": d.goals.astype(int),
        "xgoals": d.xGoals.round(2), "gsax": d.gsax.round(2), "sv_pct": d.raw_sv_pct.round(3),
        "saved_per_game": d.saved_per_game.round(3),
    }).sort_values("saved_per_game", ascending=False)
    out.to_csv(OUT, index=False)

    lg_sv = 1 - d.goals.sum() / d.shots.sum()
    print(f"Saved {len(out)} goalies to goalie_stats.csv ({season}-{(season + 1) % 100:02d} season only).")
    print(f"League: save % {lg_sv:.3f}, {shots_per_60:.1f} shots on goal per 60 min. Shrinkage K = {args.k:.0f} shots.\n")
    show = out[out.shots >= 100]
    pd.set_option("display.width", 200)
    cols = ["name", "team", "games", "shots", "sv_pct", "gsax", "saved_per_game"]
    print("Best 8 (100+ shots), saved_per_game = goals saved vs an average goalie, after shrinkage:")
    print(show.head(8)[cols].to_string(index=False))
    print("\nWorst 8:")
    print(show.tail(8)[cols].to_string(index=False))
    print("\nData: MoneyPuck.com")


if __name__ == "__main__":
    main()
