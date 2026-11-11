#!/usr/bin/env python3
"""
Download game-by-game expected goals (xG) from MoneyPuck and save xg_games.csv
for the model. Data: MoneyPuck.com (free for non-commercial use; credit them).

  py get_xg.py

Downloads all_teams.csv once per day (a large file, so the first run can take a
minute or two; if MoneyPuck is slow it gives up after 5 minutes and keeps the
previous xg_games.csv so the routine can continue), keeps last season and this season, and saves one row per game:
date, home, away, home_xg, away_xg, home_goals, away_goals.

It also prints this season's table: goals vs expected goals per team. A team
scoring far more than its xG (or allowing far less) has probably been lucky.
"""
import os
import sys
import time
from datetime import date, datetime

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
URL = "https://moneypuck.com/moneypuck/playerData/careers/gameByGame/all_teams.csv"
RAW = os.path.join(HERE, "moneypuck_all_teams.csv")
OUT = os.path.join(HERE, "xg_games.csv")
FIRST_SEASON = 2025  # MoneyPuck labels a season by its starting year: 2025 = 2025-26

# MoneyPuck team codes -> Hockey-Reference names (the names the model uses)
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
NEED = ["season", "gameDate", "playerTeam", "opposingTeam", "home_or_away",
        "situation", "xGoalsFor", "xGoalsAgainst", "goalsFor", "goalsAgainst"]


TIME_LIMIT = 300  # seconds; give up on a slow download and keep yesterday's file


def fall_back(reason):
    """Keep the existing xg_games.csv so the rest of the routine can run."""
    tmp = RAW + ".part"
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
    if os.path.exists(OUT):
        when = datetime.fromtimestamp(os.path.getmtime(OUT)).strftime("%b %d at %I:%M %p")
        print(f"\n{reason}\nKeeping the existing xg_games.csv (saved {when}). "
              "That's fine for tonight; carry on with the routine.")
        sys.exit(0)
    sys.exit(f"{reason}\nNo earlier xg_games.csv to fall back on. Run the V1 predict line only tonight.")


def download():
    fresh = (os.path.exists(RAW) and
             datetime.fromtimestamp(os.path.getmtime(RAW)).date() == date.today())
    if fresh:
        print("Using today's MoneyPuck download (already fetched today).")
        return
    print(f"Downloading MoneyPuck team data (large file; gives up after {TIME_LIMIT // 60} minutes)...")
    tmp = RAW + ".part"
    start, got, shown = time.time(), 0, 0
    try:
        with requests.get(URL, headers={"User-Agent": "Mozilla/5.0"}, stream=True, timeout=(15, 60)) as r:
            if r.status_code != 200:
                fall_back(f"MoneyPuck returned error {r.status_code}.")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
                    got += len(chunk)
                    if got - shown >= 20 << 20:  # progress every 20 MB
                        print(f"  {got / (1 << 20):.0f} MB...")
                        shown = got
                    if time.time() - start > TIME_LIMIT:
                        raise TimeoutError
    except TimeoutError:
        fall_back(f"MoneyPuck is slow right now (only {got / (1 << 20):.0f} MB in {TIME_LIMIT // 60} minutes).")
    except requests.RequestException as e:
        fall_back(f"Couldn't reach MoneyPuck ({e.__class__.__name__}).")
    except KeyboardInterrupt:
        fall_back("Download stopped.")
    os.replace(tmp, RAW)


def main():
    download()
    head = pd.read_csv(RAW, nrows=0).columns
    missing = [c for c in NEED if c not in head]
    if missing:
        sys.exit(f"MoneyPuck's file format looks different than expected. Missing columns: "
                 f"{missing}\nColumns found: {list(head)[:40]}\nPaste this message into chat.")
    df = pd.read_csv(RAW, usecols=NEED)
    df = df[(df.situation == "all") & (df.season >= FIRST_SEASON) &
            (df.home_or_away.str.upper() == "HOME")].copy()
    if df.empty:
        sys.exit("No games found for 2025-26 or later in MoneyPuck's file. Paste this into chat.")

    unknown = sorted((set(df.playerTeam) | set(df.opposingTeam)) - set(TEAMS))
    if unknown:
        print(f"Warning: unknown MoneyPuck team codes skipped: {unknown}")
    df = df[df.playerTeam.isin(TEAMS) & df.opposingTeam.isin(TEAMS)]

    games = pd.DataFrame({
        "date": pd.to_datetime(df.gameDate.astype(str), format="%Y%m%d").dt.date,
        "season": df.season,
        "home": df.playerTeam.map(TEAMS),
        "away": df.opposingTeam.map(TEAMS),
        "home_xg": df.xGoalsFor.round(3),
        "away_xg": df.xGoalsAgainst.round(3),
        "home_goals": df.goalsFor,
        "away_goals": df.goalsAgainst,
    }).sort_values("date")
    games.to_csv(OUT, index=False)
    print(f"Saved {len(games)} games to xg_games.csv "
          f"({(games.season == games.season.max()).sum()} from this season).\n")

    cur = games[games.season == games.season.max()]
    rows = []
    for team in sorted(set(cur.home) | set(cur.away)):
        h, a = cur[cur.home == team], cur[cur.away == team]
        gp = len(h) + len(a)
        gf = h.home_goals.sum() + a.away_goals.sum()
        ga = h.away_goals.sum() + a.home_goals.sum()
        xgf = h.home_xg.sum() + a.away_xg.sum()
        xga = h.away_xg.sum() + a.home_xg.sum()
        rows.append({"team": team, "GP": gp, "GF/gm": gf / gp, "xGF/gm": xgf / gp,
                     "GA/gm": ga / gp, "xGA/gm": xga / gp,
                     "xG%": 100 * xgf / (xgf + xga),
                     "luck/gm": ((gf - xgf) - (ga - xga)) / gp})
    t = pd.DataFrame(rows).sort_values("xG%", ascending=False)
    pd.set_option("display.width", 200)
    print("This season, all situations (sorted by share of expected goals):")
    print(t.to_string(index=False, float_format=lambda x: f"{x:5.2f}"))
    print("\nluck/gm = (goals - xG for) minus (goals - xG against), per game. Big positive = "
          "results better than chances; expect it to fade.\nWith only a few games per "
          "team, every number here is very noisy.\nData: MoneyPuck.com")


if __name__ == "__main__":
    main()
