#!/usr/bin/env python3
"""
Automatic V3 runs for GitHub Actions (or your own computer).

  python auto_run.py                 # "auto": does whatever is due right now
  python auto_run.py --mode morning  # scores, grading, MoneyPuck refresh, dashboard
  python auto_run.py --mode lock-now # pick every game today that hasn't started (2 API credits)
  python auto_run.py --mode dashboard

How "auto" decides (all times US Eastern):
  * Morning (first run after 6 AM): update scores from the NHL's own feed, grade every
    picks file, refresh MoneyPuck expected goals and goalie stats, rebuild the dashboard.
  * Every run: read today's NHL schedule (free). Any game starting within LOCK_MINUTES
    that hasn't been picked yet is "due". If something is due, pull BetMGM odds once
    (2 credits), pull starting goalies, run V3 on the due games (plus any other game
    starting within CLUSTER_MINUTES of the first one, so close start times share one
    odds pull), append the picks to picks_v3_DATE.txt and rebuild the dashboard.
  * A game whose start passes without a pick is skipped (never picked late).

Needs the ODDS_API_KEY environment variable (a GitHub secret).
State is kept in state.json so each game is picked exactly once.
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ET = ZoneInfo("America/New_York")
DATA = os.path.join(HERE, "data")
STATE = os.path.join(HERE, "state.json")
NHL = "https://api-web.nhle.com/v1"
ODDS_URL = "https://api.the-odds-api.com/v4/sports/icehockey_nhl/odds"
BOOK = "betmgm"

LOCK_MINUTES = 150     # pick a game once it is this close to puck drop (GitHub runs only ~hourly)
CLUSTER_MINUTES = 45   # ...and any game starting within this many minutes after it
MAX_PULLS_PER_DAY = 8  # odds pulls per day (2 credits each): 8 x 2 x 31 days = 496, under the 500 free
PREV_SEASON, CURR_SEASON = "20252026", "20262027"

V3_ARGS = ["--alpha", "0.03", "--half-life", "90", "--xg-file", "xg_games.csv",
           "--goalies", "goalies.csv", "--goalie-stats", "goalie_stats.csv",
           "--prev", os.path.join("data", "games_prev.csv"),
           "--curr", os.path.join("data", "games_curr.csv")]

ABBR = {
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


def key(name):
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s.lower())


TEAM_BY_KEY = {key(t): t for t in ABBR.values()}
TEAM_BY_KEY[key("Utah Hockey Club")] = "Utah Mammoth"


def team_name(s):
    return TEAM_BY_KEY.get(key(s))


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- state
def load_state():
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    return {"morning_done": "", "locked": {}, "odds_credits_remaining": None}


def save_state(st):
    # keep only the last 10 days of locked games
    days = sorted(st.get("locked", {}))
    st["locked"] = {d: st["locked"][d] for d in days[-10:]}
    pd_ = st.get("odds_pulls", {})
    st["odds_pulls"] = {d: pd_[d] for d in sorted(pd_)[-10:]}
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2, sort_keys=True)


# ---------------------------------------------------------------- NHL feed
def nhl_get(path, tries=3):
    for i in range(tries):
        try:
            r = requests.get(f"{NHL}/{path}", headers={"User-Agent": "nhl-paper-model"}, timeout=30)
            if r.status_code == 200:
                return r.json()
            log(f"  NHL feed {path}: HTTP {r.status_code}")
        except requests.RequestException as e:
            log(f"  NHL feed {path}: {e.__class__.__name__}")
        time.sleep(2 * (i + 1))
    return None


def season_games(season):
    """Every finished regular-season game of a season, from the 32 team schedules."""
    games = {}
    for ab in ABBR:
        js = nhl_get(f"club-schedule-season/{ab}/{season}")
        if not js:
            continue
        for g in js.get("games", []):
            if g.get("gameType") != 2 or g.get("gameState") not in ("OFF", "FINAL"):
                continue
            h, a = g.get("homeTeam", {}), g.get("awayTeam", {})
            home, away = ABBR.get(h.get("abbrev")), ABBR.get(a.get("abbrev"))
            if not home or not away or h.get("score") is None or a.get("score") is None:
                continue
            last = (g.get("gameOutcome") or {}).get("lastPeriodType", "REG")
            games[g["id"]] = {"date": g["gameDate"], "time": "", "away": away, "away_g": int(a["score"]),
                              "home": home, "home_g": int(h["score"]),
                              "ot": last if last in ("OT", "SO") else "",
                              "att": "", "len": "", "notes": "at neutral site" if g.get("neutralSite") else ""}
    df = pd.DataFrame(list(games.values()))
    return df.sort_values("date").reset_index(drop=True) if len(df) else df


def update_scores():
    os.makedirs(DATA, exist_ok=True)
    prev = os.path.join(DATA, "games_prev.csv")
    if not os.path.exists(prev):
        log("Building last season's results (one time)...")
        g = season_games(PREV_SEASON)
        if len(g) > 1000:
            g.to_csv(prev, index=False)
            log(f"  {len(g)} games saved to data/games_prev.csv")
        else:
            log(f"  Only {len(g)} games found for last season; not saved.")
    log("Updating this season's results from the NHL feed...")
    cur = season_games(CURR_SEASON)
    if len(cur) == 0:
        log("  No games returned; keeping the existing files.")
        return False
    old_path = os.path.join(HERE, "scores.csv")
    if os.path.exists(old_path):  # sanity check against the previous scores
        old = pd.read_csv(old_path, dtype={"date": str})
        m = old.merge(cur, on=["date", "home", "away"], suffixes=("_old", ""))
        bad = m[(m.home_g_old != m.home_g) | (m.away_g_old != m.away_g)]
        log(f"  Checked {len(m)} games against the previous scores.csv: {len(bad)} differ.")
        for r in bad.head(5).itertuples():
            log(f"    {r.date} {r.away} @ {r.home}: was {r.away_g_old}-{r.home_g_old}, now {r.away_g}-{r.home_g}")
        ties = cur[(cur.home_g == cur.away_g)]
        if len(ties):
            log(f"  Warning: {len(ties)} finished games show a tied score (shootout goal missing?).")
    cur.to_csv(os.path.join(DATA, "games_curr.csv"), index=False)
    cur[["date", "away", "away_g", "home", "home_g", "ot"]].to_csv(old_path, index=False)
    log(f"  {len(cur)} games this season, latest {cur.date.max()}.")
    return True


def todays_games(day):
    """Today's NHL games: away, home, start (UTC datetime)."""
    js = nhl_get(f"schedule/{day}")
    out = []
    if not js:
        return out
    for wk in js.get("gameWeek", []):
        if wk.get("date") != day:
            continue
        for g in wk.get("games", []):
            if g.get("gameType") not in (2, 3):
                continue
            home, away = ABBR.get(g["homeTeam"].get("abbrev")), ABBR.get(g["awayTeam"].get("abbrev"))
            if not home or not away:
                continue
            start = datetime.fromisoformat(g["startTimeUTC"].replace("Z", "+00:00"))
            out.append({"away": away, "home": home, "start": start, "state": g.get("gameState", "")})
    return sorted(out, key=lambda g: g["start"])


# ---------------------------------------------------------------- odds
def pull_odds(day, st):
    """BetMGM moneylines + totals for today's games (2 credits). Writes odds.csv and odds_history."""
    api_key = os.environ.get("ODDS_API_KEY", "").strip()
    if not api_key:
        log("ODDS_API_KEY is not set; can't pull odds.")
        return {}
    pulls = st.setdefault("odds_pulls", {})
    if pulls.get(day, 0) >= MAX_PULLS_PER_DAY:
        log(f"Daily cap reached ({MAX_PULLS_PER_DAY} odds pulls today); no more pulls until tomorrow.")
        return {}
    pulls[day] = pulls.get(day, 0) + 1  # count every request, even a failed one
    try:
        r = requests.get(ODDS_URL, timeout=30, params={
            "apiKey": api_key, "regions": "us", "markets": "h2h,totals",
            "oddsFormat": "american", "bookmakers": BOOK})
    except requests.RequestException as e:
        log(f"Odds API unreachable ({e.__class__.__name__}).")
        return {}
    if r.status_code != 200:
        log(f"Odds API error {r.status_code}: {r.text[:200]}")
        return {}
    st["odds_credits_remaining"] = r.headers.get("x-requests-remaining")
    log(f"Odds pulled ({pulls[day]} of {MAX_PULLS_PER_DAY} today). "
        f"API credits remaining this month: {st['odds_credits_remaining']}")
    pulled = datetime.now(ET)
    rows = {}
    for ev in r.json():
        start = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        if start.astimezone(ET).strftime("%Y-%m-%d") != day:
            continue
        home, away = team_name(ev["home_team"]), team_name(ev["away_team"])
        if not home or not away:
            log(f"  Unknown team name from the odds feed: {ev['away_team']} @ {ev['home_team']}")
            continue
        book = next((b for b in ev.get("bookmakers", []) if b["key"] == BOOK), None)
        if book is None:
            continue
        mk = {m["key"]: m["outcomes"] for m in book["markets"]}
        ml = {team_name(o["name"]): o["price"] for o in mk.get("h2h", [])}
        if home not in ml or away not in ml:
            continue
        row = {"pulled_at": pulled.strftime("%Y-%m-%d %H:%M"),
               "start": start.astimezone(ET).strftime("%Y-%m-%d %H:%M"), "book": BOOK,
               "home": home, "away": away, "home_ml": ml[home], "away_ml": ml[away],
               "total_line": "", "over": "", "under": ""}
        tot = {o["name"]: o for o in mk.get("totals", [])}
        if "Over" in tot and "Under" in tot and tot["Over"].get("point") == tot["Under"].get("point"):
            row.update(total_line=tot["Over"]["point"], over=tot["Over"]["price"], under=tot["Under"]["price"])
        rows[(away, home)] = row
    if rows:
        hist = os.path.join(HERE, "odds_history")
        os.makedirs(hist, exist_ok=True)
        fields = list(next(iter(rows.values())))
        for path in (os.path.join(hist, f"odds_{day}_{pulled:%H%M}.csv"), os.path.join(HERE, "odds.csv")):
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writeheader()
                w.writerows(rows.values())
    log(f"  {len(rows)} games today with BetMGM lines.")
    return rows


# ---------------------------------------------------------------- phone notifications (ntfy)
def site_url():
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner.lower()}.github.io/{name}/"
    return ""


def notify(title, message):
    """Push a message to the ntfy app. Needs the NTFY_TOPIC secret; silently skipped without it."""
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return
    title = title.replace("\u2013", "-").encode("ascii", "ignore").decode()  # headers must be plain ASCII
    headers = {"Title": title, "Tags": "ice_hockey"}
    if site_url():
        headers["Click"] = site_url()
    try:
        r = requests.post(f"https://ntfy.sh/{topic}", data=message.encode("utf-8"), headers=headers, timeout=20)
        log(f"Notification sent ({r.status_code}).")
    except requests.RequestException as e:
        log(f"Notification failed ({e.__class__.__name__}).")


def _odds(o):
    return f"+{o}" if o > 0 else str(o)


def picks_message(out, have):
    """Summarize the flagged bets from one V3 run."""
    from grade_picks import parse_line
    bets = [p for p in (parse_line(l) for l in out.splitlines()) if p and p["stake_pct"] > 0]
    times = sorted({g["start"].astimezone(ET).strftime("%I:%M %p").lstrip("0") for g in have})
    title = f"V3 picks for {', '.join(times)} ET games"
    if not bets:
        return title, f"No bets with a 3+ point edge in {len(have)} game(s)."
    lines = []
    for p in sorted(bets, key=lambda p: -p["edge"]):
        what = p["bet"] if p["side"] == "ML" else f"{p['away'].split()[-1]} @ {p['home'].split()[-1]} {p['bet']}"
        flag = "  (7+ edge, extra doubt)" if p["edge"] >= 0.07 else ""
        lines.append(f"{what} ({_odds(p['odds'])}), edge {100 * p['edge']:+.1f}{flag}")
    return title, "\n".join(lines) + "\nPaper picks only."


def morning_message(day):
    path = os.path.join(HERE, "graded_picks.csv")
    if not os.path.exists(path):
        return None
    g = pd.read_csv(path, dtype={"date": str})
    g = g[(g.model == "v3") & g.result.isin(["W", "L", "P"])]
    if g.empty:
        return None
    last = g[g.date == g[g.date < day].date.max()] if (g.date < day).any() else g.iloc[0:0]
    def rec(x):
        w, l, p = (x.result == "W").sum(), (x.result == "L").sum(), (x.result == "P").sum()
        return f"{w}\u2013{l}" + (f"\u2013{p}" if p else "") + f", {x.units.sum():+.2f} units"
    if last.empty:
        return None
    d = pd.Timestamp(last.date.iloc[0])
    return (f"V3 last night ({d:%b} {d.day}): {rec(last)}",
            f"Season: {rec(g)}\nTonight's picks arrive about 1\u20132 hours before each game.")


# ---------------------------------------------------------------- steps
def run(cmd, out_path=None, append=False):
    """Run a project script. Failures are reported but never stop the whole run."""
    log(f"$ {' '.join(cmd)}")
    res = subprocess.run([sys.executable] + cmd, cwd=HERE, capture_output=True, text=True)
    if res.stderr.strip():
        log(res.stderr.strip()[-3000:])
    if out_path is None:
        if res.stdout.strip():
            log(res.stdout.strip()[-3000:])
    elif res.returncode == 0:
        with open(os.path.join(HERE, out_path), "a" if append else "w", encoding="utf-8") as f:
            f.write(res.stdout)
    if res.returncode != 0:
        log(f"  (exit code {res.returncode})")
    return res.returncode == 0, res.stdout


def write_slate(day, games):
    with open(os.path.join(HERE, "slate.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "start_utc", "away", "home"])
        for g in games:
            w.writerow([day, g["start"].strftime("%Y-%m-%dT%H:%M:%SZ"), g["away"], g["home"]])


def morning(day, st):
    log(f"=== Morning run for {day} ===")
    if update_scores():
        env_ok = True
    else:
        env_ok = os.path.exists(os.path.join(DATA, "games_curr.csv"))
    if env_ok:
        run(["grade_picks.py"])
    run(["get_xg.py"])
    run(["get_goalie_stats.py"])
    st["morning_done"] = day
    msg = morning_message(day)
    if msg:
        notify(*msg)


def fresh_goalies(day):
    """Run get_goalies.py; if it fails or returns another day, leave an empty file so V3 never
    uses stale starters."""
    ok, _ = run(["get_goalies.py"])
    path = os.path.join(HERE, "goalies.csv")
    good = False
    if ok and os.path.exists(path):
        try:
            good = (pd.read_csv(path, dtype=str).date == day).any()
        except Exception:
            good = False
    if not good:
        log("  No starting goalies for today: V3 makes no goalie adjustment for these games.")
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["date", "start_utc", "away", "home", "away_goalie", "away_status",
                                    "home_goalie", "home_status", "pulled"])


def lock(day, games, due, st):
    odds = pull_odds(day, st)
    if not odds:
        log("No odds available; will try again next run.")
        return False
    fresh_goalies(day)
    have = [g for g in due if (g["away"], g["home"]) in odds]
    missing = [g for g in due if (g["away"], g["home"]) not in odds]
    for g in missing:
        log(f"  No BetMGM line yet for {g['away']} @ {g['home']}; will retry next run.")
    if not have:
        return False
    fields = ["home", "away", "home_ml", "away_ml", "total_line", "over", "under"]
    with open(os.path.join(HERE, "odds_lock.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for g in have:
            w.writerow(odds[(g["away"], g["home"])])
    now = datetime.now(ET)
    picks = f"picks_v3_{day}.txt"
    header = (f"\nLocked {now:%I:%M %p} ET: "
              + "; ".join(f"{g['away']} at {g['home']} ({g['start'].astimezone(ET):%I:%M %p})" for g in have) + "\n")
    ok, out = run(["nhl_model.py", "predict", "--odds", "odds_lock.csv"] + V3_ARGS)
    if not ok:
        log("V3 failed; nothing locked.")
        return False
    with open(os.path.join(HERE, picks), "a", encoding="utf-8") as f:
        f.write(header + out)
    log(out)
    notify(*picks_message(out, have))
    st.setdefault("locked", {}).setdefault(day, []).extend(f"{g['away']} @ {g['home']}" for g in have)
    return True


def dashboard():
    env = dict(os.environ, NHL_FOCUS="v3", NHL_OUT=os.path.join(HERE, "docs", "index.html"))
    os.makedirs(os.path.join(HERE, "docs"), exist_ok=True)
    open(os.path.join(HERE, "docs", ".nojekyll"), "a").close()
    log("$ make_dashboard.py")
    res = subprocess.run([sys.executable, "make_dashboard.py"], cwd=HERE, env=env, capture_output=True, text=True)
    log((res.stdout + res.stderr).strip()[-2000:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="auto", choices=["auto", "morning", "lock-now", "dashboard"])
    args = ap.parse_args()
    os.environ["NHL_SCORES_FILE"] = os.path.join(HERE, "scores.csv")  # grade from the NHL feed
    now = datetime.now(timezone.utc)
    day = now.astimezone(ET).strftime("%Y-%m-%d")
    st = load_state()
    changed = False

    if args.mode == "dashboard":
        dashboard()
        return
    if args.mode == "morning" or (args.mode == "auto" and st.get("morning_done") != day
                                  and now.astimezone(ET).hour >= 6):
        morning(day, st)
        changed = True

    games = todays_games(day)
    write_slate(day, games)  # empty on days with no games
    done = set(st.get("locked", {}).get(day, []))
    pf = os.path.join(HERE, f"picks_v3_{day}.txt")
    if os.path.exists(pf):  # games already in today's file (e.g. picked on your computer) count as done
        with open(pf, encoding="utf-8", errors="replace") as f:
            text = f.read()
        done |= {f"{g['away']} @ {g['home']}" for g in games if f"{g['away']} @ {g['home']}" in text}
    open_games = [g for g in games if f"{g['away']} @ {g['home']}" not in done and g["start"] > now]
    if args.mode == "lock-now":
        due = open_games
    else:
        first = [g for g in open_games if g["start"] - now <= timedelta(minutes=LOCK_MINUTES)]
        if first:
            cutoff = min(g["start"] for g in first) + timedelta(minutes=CLUSTER_MINUTES)
            due = [g for g in open_games if g["start"] <= max(cutoff, max(g2["start"] for g2 in first))]
        else:
            due = []
    nxt = [g for g in open_games if g not in due]
    log(f"{len(games)} games today; {len(done)} already picked; {len(due)} due now"
        + (f"; next: {nxt[0]['away']} @ {nxt[0]['home']} at {nxt[0]['start'].astimezone(ET):%I:%M %p} ET"
           if nxt else "") + ".")
    if due and lock(day, games, due, st):
        changed = True
    if changed:
        dashboard()
    save_state(st)


if __name__ == "__main__":
    main()
