#!/usr/bin/env python3
"""
Pull today's projected starting goalies from Daily Faceoff.

  py get_goalies.py                  # today
  py get_goalies.py --date 2026-10-03

Saves goalies.csv (and a dated copy in goalies_history), then lists the goalies
for the games in odds.csv and flags any starter that isn't confirmed yet.
Run it once a day, after get_odds.py. It uses no API credits.

Statuses: Confirmed = team announced it; Likely = strong report (e.g. morning
skate); Unconfirmed = Daily Faceoff's projection only.
"""
import argparse
import csv
import os
import re
import sys
import unicodedata
from datetime import date, datetime

import requests
from lxml import html as lxml_html

HERE = os.path.dirname(os.path.abspath(__file__))
URL = "https://www.dailyfaceoff.com/starting-goalies"
STATUSES = {"Confirmed", "Likely", "Unconfirmed"}

# Hockey-Reference spellings, which the rest of the project uses
TEAMS = [
    "Anaheim Ducks", "Boston Bruins", "Buffalo Sabres", "Calgary Flames",
    "Carolina Hurricanes", "Chicago Blackhawks", "Colorado Avalanche",
    "Columbus Blue Jackets", "Dallas Stars", "Detroit Red Wings", "Edmonton Oilers",
    "Florida Panthers", "Los Angeles Kings", "Minnesota Wild", "Montreal Canadiens",
    "Nashville Predators", "New Jersey Devils", "New York Islanders",
    "New York Rangers", "Ottawa Senators", "Philadelphia Flyers",
    "Pittsburgh Penguins", "San Jose Sharks", "Seattle Kraken", "St. Louis Blues",
    "Tampa Bay Lightning", "Toronto Maple Leafs", "Utah Mammoth",
    "Vancouver Canucks", "Vegas Golden Knights", "Washington Capitals",
    "Winnipeg Jets",
]


def key(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s.lower())


TEAM_BY_KEY = {key(t): t for t in TEAMS}


def team(s):
    return TEAM_BY_KEY.get(key(s))


def tokens_from(page):
    doc = lxml_html.fromstring(page)
    for bad in doc.xpath("//script|//style|//noscript"):
        bad.drop_tree()
    return [t.strip() for t in doc.itertext() if t.strip()]


def parse(tokens):
    games, cur = [], None
    i = 0
    while i < len(tokens):
        t = tokens[i]
        header = None
        m = re.match(r"^(.+?)\s+(?:at|@)\s+(.+)$", t)
        if m and team(m[1]) and team(m[2]):
            header, step = (team(m[1]), team(m[2])), 1
        elif (i + 2 < len(tokens) and team(t) and tokens[i + 1] in ("at", "@")
              and team(tokens[i + 2])):
            header, step = (team(t), team(tokens[i + 2])), 3
        if header:
            cur = {"away": header[0], "home": header[1], "start_utc": "", "goalies": []}
            games.append(cur)
            i += step
            continue
        if cur is not None:
            if not cur["start_utc"] and re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", t):
                cur["start_utc"] = t
            elif t in STATUSES and len(cur["goalies"]) < 2 and i > 0:
                updated = tokens[i + 1] if i + 1 < len(tokens) and re.match(
                    r"^\d{4}-\d{2}-\d{2}T", tokens[i + 1]) else ""
                cur["goalies"].append((tokens[i - 1], t, updated))
        i += 1
    return [g for g in games if len(g["goalies"]) == 2]


def local_time(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%I:%M %p")
    except ValueError:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (default: today)")
    args = ap.parse_args()
    day = args.date or date.today().isoformat()
    url = URL if not args.date else f"{URL}/{day}"

    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    if r.status_code != 200:
        sys.exit(f"Daily Faceoff returned error {r.status_code}. Try again later, or open {url} in a browser.")
    tokens = tokens_from(r.text)
    games = parse(tokens)
    if not games:
        dbg = os.path.join(HERE, "goalies_debug.txt")
        with open(dbg, "w", encoding="utf-8") as f:
            f.write("\n".join(tokens))
        sys.exit("Couldn't find any games on the page. The page layout may have changed.\n"
                 f"Saved what the script saw to {dbg}; paste the first ~150 lines into chat.")

    rows = []
    for g in games:
        (ag, ast, aup), (hg, hst, hup) = g["goalies"]
        rows.append({"date": day, "start_utc": g["start_utc"], "away": g["away"], "home": g["home"],
                     "away_goalie": ag, "away_status": ast, "home_goalie": hg, "home_status": hst,
                     "pulled": datetime.now().strftime("%Y-%m-%d %H:%M")})
    fields = list(rows[0])
    hist = os.path.join(HERE, "goalies_history")
    os.makedirs(hist, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    for path in (os.path.join(HERE, "goalies.csv"), os.path.join(hist, f"goalies_{stamp}.csv")):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)

    print(f"{len(rows)} games found on Daily Faceoff ({day}), saved to goalies.csv:\n")
    for r_ in rows:
        print(f"  {local_time(r_['start_utc']):>8}  {r_['away']}: {r_['away_goalie']} ({r_['away_status']})"
              f"  @  {r_['home']}: {r_['home_goalie']} ({r_['home_status']})")

    # check against tonight's odds
    odds_path = os.path.join(HERE, "odds.csv")
    if os.path.exists(odds_path):
        by_game = {(r_["away"], r_["home"]): r_ for r_ in rows}
        with open(odds_path, encoding="utf-8") as f:
            odds = list(csv.DictReader(f))
        print("\nGames in odds.csv:")
        for o in odds:
            g = by_game.get((o["away"], o["home"]))
            if not g:
                print(f"  {o['away']} @ {o['home']}: no goalie info found")
                continue
            unsure = [f"{g[s + '_goalie']} ({g[s + '_status']})" for s in ("away", "home")
                      if g[s + "_status"] != "Confirmed"]
            note = "both confirmed" if not unsure else "NOT CONFIRMED: " + ", ".join(unsure)
            print(f"  {o['away']} @ {o['home']}: {note}")
    print("\nIf a starter changes from what was expected (e.g. a backup), treat that pick with extra doubt.")


if __name__ == "__main__":
    main()
