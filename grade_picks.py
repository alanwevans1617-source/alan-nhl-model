#!/usr/bin/env python3
"""
Grade paper picks automatically.

  py grade_picks.py

Reads every picks_YYYY-MM-DD.txt file in this folder (the saved output of
`nhl_model.py predict`), downloads final scores from Hockey-Reference, and grades
every bet the model flagged (stake_pct > 0) at a flat 1 unit each.
If Hockey-Reference is missing a score (it sometimes runs a day or two behind),
the score is filled in from the NHL's own public scores feed (api-web.nhle.com).

Rules (standard sportsbook rules):
  - Moneyline includes overtime and shootout.
  - Totals use the final score as listed, so a shootout win counts as one goal.
  - Total exactly on the line = push (stake refunded).

Writes graded_picks.csv (rebuilt from scratch every run) and prints a summary.
"""
import glob
import io
import os
import re
import sys

import pandas as pd
import requests

SCORES_URL = "https://www.hockey-reference.com/leagues/NHL_2027_games.html"
HERE = os.path.dirname(os.path.abspath(__file__))
ROW = re.compile(r"^(?P<text>.*?)\s+(?P<odds>[+-]?\d+)\s+(?P<model_p>[\d.]+)\s+"
                 r"(?P<fair_p>[\d.]+)\s+(?P<edge>-?[\d.]+)\s+(?P<ev>-?[\d.]+)\s+"
                 r"(?P<stake>[\d.]+)\s*$")


NHL_URL = "https://api-web.nhle.com/v1/score/{}"
ABBR = {  # NHL abbreviation -> Hockey-Reference team name
    "ANA": "Anaheim Ducks", "BOS": "Boston Bruins", "BUF": "Buffalo Sabres", "CGY": "Calgary Flames",
    "CAR": "Carolina Hurricanes", "CHI": "Chicago Blackhawks", "COL": "Colorado Avalanche",
    "CBJ": "Columbus Blue Jackets", "DAL": "Dallas Stars", "DET": "Detroit Red Wings",
    "EDM": "Edmonton Oilers", "FLA": "Florida Panthers", "LAK": "Los Angeles Kings",
    "MIN": "Minnesota Wild", "MTL": "Montreal Canadiens", "NSH": "Nashville Predators",
    "NJD": "New Jersey Devils", "NYI": "New York Islanders", "NYR": "New York Rangers",
    "OTT": "Ottawa Senators", "PHI": "Philadelphia Flyers", "PIT": "Pittsburgh Penguins",
    "SJS": "San Jose Sharks", "SEA": "Seattle Kraken", "STL": "St. Louis Blues",
    "TBL": "Tampa Bay Lightning", "TOR": "Toronto Maple Leafs", "UTA": "Utah Mammoth",
    "VAN": "Vancouver Canucks", "VGK": "Vegas Golden Knights", "WSH": "Washington Capitals",
    "WPG": "Winnipeg Jets",
}


def load_nhl_day(day):
    """Final scores for one date from the NHL's public feed, in the same shape as Hockey-Reference."""
    try:
        r = requests.get(NHL_URL.format(day), headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        r.raise_for_status()
        games = r.json().get("games", [])
    except Exception as e:
        print(f"  NHL feed unavailable for {day} ({e.__class__.__name__}).")
        return []
    rows = []
    for gm in games:
        if gm.get("gameState") not in ("OFF", "FINAL"):
            continue
        h, a = gm.get("homeTeam", {}), gm.get("awayTeam", {})
        home, away = ABBR.get(h.get("abbrev")), ABBR.get(a.get("abbrev"))
        if not home or not away or h.get("score") is None or a.get("score") is None:
            continue
        last = (gm.get("gameOutcome") or {}).get("lastPeriodType", "REG")
        rows.append({"date": pd.Timestamp(gm.get("gameDate", day)).date(), "away": away,
                     "away_g": int(a["score"]), "home": home, "home_g": int(h["score"]),
                     "ot": last if last in ("OT", "SO") else ""})
    return rows


def load_scores(days=()):
    """Hockey-Reference scores, with any missing finals on `days` filled from the NHL feed."""
    local = os.environ.get("NHL_SCORES_FILE")
    if local and os.path.exists(local):  # automated runs: scores already pulled from the NHL feed
        df = pd.read_csv(local, dtype={"date": str})
        df["ot"] = df.ot.fillna("") if "ot" in df else ""
        df["date"] = pd.to_datetime(df.date).dt.date
        df = df[["date", "away", "away_g", "home", "home_g", "ot"]]
        print(f"Using {len(df)} final scores from {os.path.basename(local)}.")
        return {(r.date, r.home, r.away): r for r in df.itertuples()}
    try:
        scores = load_hr()
    except Exception as e:
        print(f"Hockey-Reference unavailable ({e.__class__.__name__}); using the NHL feed only.")
        scores = {}
    filled = 0
    for day in sorted(set(days)):
        have = [v for k, v in scores.items() if k[0] == day]
        if have and all(pd.notna(v.home_g) for v in have):
            continue  # Hockey-Reference already has every final for this date
        rows = load_nhl_day(day)
        if rows:
            for r in pd.DataFrame(rows).itertuples():
                old = scores.get((r.date, r.home, r.away))
                if old is None or pd.isna(old.home_g):
                    scores[(r.date, r.home, r.away)] = r
                    filled += 1
    if filled:
        print(f"Filled {filled} final score(s) from the NHL feed (Hockey-Reference was behind).")
    return scores


def save_scores(scores):
    """Every final score this season -> scores.csv (the dashboard's storylines use it offline)."""
    done = [{"date": v.date, "away": v.away, "away_g": int(v.away_g), "home": v.home,
             "home_g": int(v.home_g), "ot": v.ot} for v in scores.values() if pd.notna(v.home_g)]
    if done:
        pd.DataFrame(done).sort_values("date").to_csv(os.path.join(HERE, "scores.csv"), index=False)


def load_hr():
    r = requests.get(SCORES_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    raw = pd.read_html(io.StringIO(r.text))[0]
    df = pd.DataFrame({
        "date": pd.to_datetime(raw.iloc[:, 0], errors="coerce").dt.date,
        "away": raw.iloc[:, 2], "away_g": pd.to_numeric(raw.iloc[:, 3], errors="coerce"),
        "home": raw.iloc[:, 4], "home_g": pd.to_numeric(raw.iloc[:, 5], errors="coerce"),
        "ot": raw.iloc[:, 6].fillna("").astype(str).str.strip(),
    })
    df = df.dropna(subset=["date"])
    return {(r.date, r.home, r.away): r for r in df.itertuples()}


def parse_line(line):
    """One row of predict output -> dict, or None for headers/blank lines."""
    m = ROW.match(line.rstrip())
    if not m or " @ " not in m["text"]:
        return None
    away, right = (s.strip() for s in m["text"].split(" @ ", 1))
    t = re.match(r"^(.*)\s+(Over|Under)\s+([\d.]+)$", right)
    if t:
        home, side, line_ = t[1].strip(), t[2], float(t[3])
    elif right.endswith(" ML"):
        side, line_ = "ML", None
        if right.endswith(f" {away} ML"):
            home, pick = right[: -len(f" {away} ML")].strip(), away
        else:  # right is "Home Home ML"
            s = right[:-3].strip()
            home = s[: (len(s) - 1) // 2].strip()
            pick = home
    else:
        return None
    return {"away": away, "home": home, "side": side, "line": line_,
            "pick": pick if side == "ML" else None,
            "bet": right[len(home):].strip(),
            "odds": int(m["odds"]), "model_p": float(m["model_p"]),
            "fair_p": float(m["fair_p"]), "edge": float(m["edge"]),
            "stake_pct": float(m["stake"])}


def profit(odds):
    return odds / 100 if odds > 0 else 100 / -odds


def grade(p, game):
    if game is None:
        return "no game found", None, ""
    if pd.isna(game.home_g) or pd.isna(game.away_g):
        return "pending", None, ""
    hg, ag = int(game.home_g), int(game.away_g)
    score = f"{game.away} {ag} - {game.home} {hg} {game.ot}".strip()
    if p["side"] == "ML":
        winner = game.home if hg > ag else game.away
        res = "W" if p["pick"] == winner else "L"
    else:
        total = hg + ag
        if total == p["line"]:
            res = "P"
        elif (total > p["line"]) == (p["side"] == "Over"):
            res = "W"
        else:
            res = "L"
    units = {"W": round(profit(p["odds"]), 3), "L": -1.0, "P": 0.0}[res]
    return res, units, score


def main():
    files = sorted(glob.glob(os.path.join(HERE, "picks_*.txt")))
    if not files:
        sys.exit("No picks_*.txt files found in this folder.")
    today = pd.Timestamp.today().date()
    days = []
    for f in files:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(f))
        if m and pd.Timestamp(m[1]).date() < today:
            days.append(pd.Timestamp(m[1]).date())
    scores = load_scores(days)
    save_scores(scores)

    rows = []
    for f in files:
        m = re.search(r"picks_(?:([a-z0-9]+)_)?(\d{4}-\d{2}-\d{2})", os.path.basename(f))
        if not m:
            continue
        model = m[1] or "main"
        day = pd.Timestamp(m[2]).date()
        with open(f, encoding="utf-8", errors="replace") as fh:
            text = fh.read().replace("\x00", "")  # handles files saved as UTF-16
        parsed = [p for p in (parse_line(l) for l in text.splitlines()) if p]
        if not parsed:
            print(f"Could not read any picks from {os.path.basename(f)} (different format?)")
            continue
        for p in parsed:
            if p["stake_pct"] <= 0:
                continue
            res, units, score = grade(p, scores.get((day, p["home"], p["away"])))
            rows.append({"date": day, "model": model, "game": f"{p['away']} @ {p['home']}",
                         "bet": p["bet"], "odds": p["odds"], "model_p": p["model_p"],
                         "fair_p": p["fair_p"], "edge": p["edge"],
                         "result": res, "units": units, "final": score})

    if not rows:
        sys.exit("No flagged picks (stake_pct > 0) found.")
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(HERE, "graded_picks.csv"), index=False)

    pd.set_option("display.width", 200)
    cols = ["date", "game", "bet", "odds", "edge", "result", "units", "final"]
    if df.model.nunique() > 1:
        cols.insert(1, "model")
    print(df[cols].to_string(index=False))

    for model in ["main"] + sorted(set(df.model) - {"main"}):
        summarize(df[df.model == model], model)
    other = df[~df.result.isin(["W", "L", "P"])]
    if not other.empty:
        print(f"\n{len(other)} pick(s) not graded yet (pending or no game found).")
    print("\nSaved: graded_picks.csv. Small samples swing wildly; judge after 100+ picks.")


def summarize(df, model):
    if df.empty:
        return
    label = {"main": "V1 (goals)", "xg": "V2 (xG)", "xgb": "V2b (xG + market blend)", "v3": "V3 (xG + starting goalie)"}.get(model, model)
    done = df[df.result.isin(["W", "L", "P"])]
    print(f"\n--- {label} summary (flat 1 unit per pick) ---")
    if done.empty:
        print("No finished games yet.")
    else:
        def line(d, label):
            w, l, pu = (d.result == "W").sum(), (d.result == "L").sum(), (d.result == "P").sum()
            u = d.units.sum()
            risked = w + l
            roi = f"{100 * u / risked:+.1f}%" if risked else "n/a"
            print(f"{label:<14} {w}-{l}-{pu}  units {u:+.2f}  ROI {roi}")
        for day, d in done.groupby("date"):
            line(d, str(day))
        d = done.copy()
        d["market"] = d.bet.str.endswith("ML").map({True: "Moneyline", False: "Totals"})
        for mk, dd in d.groupby("market"):
            line(dd, mk)
        line(done, "ALL")


if __name__ == "__main__":
    main()
