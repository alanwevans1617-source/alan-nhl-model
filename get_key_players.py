#!/usr/bin/env python3
"""
Each team's key players this season, for the injury flag.

  python get_key_players.py

Downloads MoneyPuck's skater season summary (this season only) once a day and saves
key_players.csv: each team's top 3 forwards and top 2 defensemen by ice time per game
(at least 5 games played). Data: MoneyPuck.com (non-commercial use; credit them).
"""
import io
import os
import sys
from datetime import date

import pandas as pd
import requests

from get_goalie_stats import TEAMS, key, season_start

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "key_players.csv")
URL = "https://moneypuck.com/moneypuck/playerData/seasonSummary/{}/regular/skaters.csv"
TOP_F, TOP_D, MIN_GP = 3, 2, 5


def main():
    season = season_start(date.today())
    try:
        r = requests.get(URL.format(season), headers={"User-Agent": "Mozilla/5.0"}, timeout=90)
    except requests.RequestException as e:
        sys.exit(f"Couldn't reach MoneyPuck ({e.__class__.__name__}); keeping the existing key_players.csv.")
    if r.status_code != 200:
        sys.exit(f"MoneyPuck returned error {r.status_code}; keeping the existing key_players.csv.")
    df = pd.read_csv(io.StringIO(r.text))
    need = ["name", "team", "position", "situation", "games_played", "icetime"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        sys.exit(f"MoneyPuck's skater file looks different. Missing: {missing}. Columns: {list(df.columns)[:30]}")

    d = df[(df.situation == "all") & (df.games_played >= MIN_GP)].copy()
    d["team_name"] = d.team.map(TEAMS)
    d = d[d.team_name.notna()]
    d["toi_per_game"] = d.icetime / d.games_played / 60
    d["group"] = d.position.map(lambda p: "D" if str(p).upper().startswith("D") else "F")
    rows = []
    for team, g in d.groupby("team_name"):
        for grp, n in (("F", TOP_F), ("D", TOP_D)):
            top = g[g.group == grp].sort_values("toi_per_game", ascending=False).head(n)
            for rank, p in enumerate(top.itertuples(), 1):
                rows.append({"team": team, "name": p.name, "key": key(p.name), "group": grp, "rank": rank,
                             "position": p.position, "games": int(p.games_played),
                             "toi_per_game": round(p.toi_per_game, 1)})
    out = pd.DataFrame(rows).sort_values(["team", "group", "rank"])
    out.to_csv(OUT, index=False)
    print(f"Saved {len(out)} key players for {out.team.nunique()} teams to key_players.csv "
          f"(top {TOP_F} F + top {TOP_D} D by ice time, {season}-{(season + 1) % 100:02d}). Data: MoneyPuck.com")


if __name__ == "__main__":
    main()
