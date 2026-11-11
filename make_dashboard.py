#!/usr/bin/env python3
"""
Build a dashboard of the model's paper picks and open it in your browser.

  py make_dashboard.py

Reads every picks_YYYY-MM-DD.txt (V1, goals only), picks_xg_YYYY-MM-DD.txt (V2, 50%
expected goals) and picks_xgb_YYYY-MM-DD.txt (V2b, V2 blended 50/50 with the market) file plus graded_picks.csv (results from grade_picks.py), then
writes dashboard.html in this folder. Run it last in the daily routine. It needs no
internet connection.
"""
import glob
import html
import os
import re
import webbrowser
from datetime import datetime

import pandas as pd

from grade_picks import parse_line

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("NHL_OUT") or os.path.join(HERE, "dashboard.html")
FOCUS = os.environ.get("NHL_FOCUS", "main")  # "v3" on the automated website
RETIRE_DAY = "2026-11-12"  # V1, V2 and V2b stopped when V3 went automatic
RETIRED = {"main": RETIRE_DAY, "xg": RETIRE_DAY, "xgb": RETIRE_DAY} if FOCUS == "v3" else {}
TARGET = 100  # picks needed before judging the model
NUM_HEADERS = {"Live units", "Backtest units", "Odds", "Model", "Market", "Edge", "Units", "ROI", "Avg edge", "Picks",
               "V1 edge", "V2 edge", "V2b edge", "V3 edge", "V1 units", "V2 units", "V2b units", "V3 units",
               "Graded", "Waiting"}
MODELS = ("main", "xg", "xgb", "v3")
NAMES = {"main": "V1", "xg": "V2", "xgb": "V2b", "v3": "V3"}
DESC = {"main": "goals only", "xg": "50% expected goals", "xgb": "V2 blended 50/50 with the market",
        "v3": "V2 + tonight's starting goalies"}


def nice(d, fmt):
    return datetime.strptime(d, "%Y-%m-%d").strftime(fmt)


def esc(x):
    return html.escape(str(x))


def load_lines():
    rows = []
    for f in sorted(glob.glob(os.path.join(HERE, "picks_*.txt"))):
        m = re.search(r"picks_(?:([a-z0-9]+)_)?(\d{4}-\d{2}-\d{2})", os.path.basename(f))
        if not m or (m[1] or "main") not in NAMES:
            continue
        with open(f, encoding="utf-8", errors="replace") as fh:
            text = fh.read().replace("\x00", "")
        for i, line in enumerate(text.splitlines()):
            p = parse_line(line)
            if p:
                rows.append({"model": m[1] or "main", "date": m[2], "order": i,
                             "game": f"{p['away']} @ {p['home']}", "bet": p["bet"],
                             "odds": p["odds"], "model_p": p["model_p"],
                             "fair_p": p["fair_p"], "edge": p["edge"],
                             "flagged": p["stake_pct"] > 0})
    return pd.DataFrame(rows)


def load_results():
    path = os.path.join(HERE, "graded_picks.csv")
    if not os.path.exists(path):
        return {}
    g = pd.read_csv(path, dtype={"date": str})
    if "model" not in g:
        g["model"] = "main"
    return {(r.model, r.date, r.game, r.bet): (r.result, r.units, r.final)
            for r in g.itertuples()}


def record(d):
    w, l, p = (d.result == "W").sum(), (d.result == "L").sum(), (d.result == "P").sum()
    u = d.units.sum()
    roi = 100 * u / (w + l) if (w + l) else None
    return w, l, p, u, roi


def fmt_units(u):
    return f"{u:+.2f}".replace("-", "\u2212")


def fmt_roi(r):
    return "\u2014" if r is None else f"{r:+.1f}%".replace("-", "\u2212")


def fmt_odds(o):
    return f"+{o}" if o > 0 else f"\u2212{-o}"


def sign_class(v):
    return "pos" if v > 0 else "neg" if v < 0 else ""


def fmt_edge(e):
    return "\u2014" if e is None or pd.isna(e) else f"{100*e:+.1f}".replace("-", "\u2212")


def fmt_rec(w, l, p):
    return f"{w}\u2013{l}" + (f"\u2013{p}" if p else "")


def h2h_svg(daily):
    """Running units at the end of each day, one line per model, from the first V2 day."""
    dates = sorted({d for s in daily.values() for d in s})
    if not dates:
        return '<p class="empty">The head-to-head chart appears once V2 picks are graded.</p>'
    W, H, L, R, T, B = 760, 220, 44, 40, 16, 34
    series = {}
    for m, s in daily.items():  # carry totals forward on days a model had no graded picks
        cum, out = 0.0, [0.0]
        for d in dates:
            cum = s.get(d, cum)
            out.append(cum)
        series[m] = out
    vals = [v for s in series.values() for v in s]
    lo, hi = min(vals + [-1]), max(vals + [1])
    pad = (hi - lo) * 0.15
    lo, hi = lo - pad, hi + pad
    n = len(dates)
    x = lambda i: L + (W - L - R) * i / max(n, 1)
    y = lambda v: T + (H - T - B) * (hi - v) / (hi - lo)
    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Running units by day for each version since V2 started">']
    step = max(1, round((hi - lo) / 5))
    v = int(lo // step) * step
    while v <= hi:
        if v != 0 and v >= lo:
            parts.append(f'<line class="grid" x1="{L}" x2="{W-R}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
                         f'<text class="tick" x="{L-8}" y="{y(v)+4:.1f}" text-anchor="end">{fmt_units(v).replace(".00","")}</text>')
        v += step
    parts.append(f'<line class="zero" x1="{L}" x2="{W-R}" y1="{y(0):.1f}" y2="{y(0):.1f}"/>'
                 f'<text class="tick" x="{L-8}" y="{y(0)+4:.1f}" text-anchor="end">0</text>')
    for i, d in enumerate(dates, start=1):
        parts.append(f'<text class="tick" x="{x(i):.1f}" y="{H-12}" text-anchor="middle">{nice(d, "%b")} {int(d[8:])}</text>')
    for m, s in series.items():
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(s))
        parts.append(f'<polyline class="h2h {m}" points="{pts}"/>')
        for i, v in enumerate(s[1:], start=1):
            parts.append(f'<circle class="h2hdot {m}" cx="{x(i):.1f}" cy="{y(v):.1f}" r="3.5">'
                         f'<title>{NAMES[m]} after {dates[i-1]}: {fmt_units(v)}</title></circle>')
    ends = dict(sorted(((m, y(s[-1])) for m, s in series.items()), key=lambda kv: kv[1]))
    prev = None
    for m in ends:  # keep end labels at least 15px apart
        if prev is not None and ends[m] - prev < 15:
            ends[m] = prev + 15
        prev = ends[m]
    for m, ly in ends.items():
        parts.append(f'<text class="lbl {m}" x="{x(n)+8:.1f}" y="{ly+4:.1f}">{NAMES[m]}</text>')
    parts.append("</svg>")
    return "".join(parts)


def chart_svg(done):
    """Running units after each graded pick, with a zero line."""
    if done.empty:
        return '<p class="empty">The chart appears once the first picks are graded.</p>'
    cum = done.units.cumsum().tolist()
    ys = [0.0] + cum
    n = len(ys) - 1
    W, H, L, R, T, B = 760, 260, 44, 16, 16, 34
    lo, hi = min(ys + [-1]), max(ys + [1])
    pad = (hi - lo) * 0.12
    lo, hi = lo - pad, hi + pad
    x = lambda i: L + (W - L - R) * i / max(n, 1)
    y = lambda v: T + (H - T - B) * (hi - v) / (hi - lo)
    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(ys))
    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Running units won or lost after each graded pick">']
    # horizontal grid at whole units
    step = max(1, round((hi - lo) / 5))
    v = int(lo // step) * step
    while v <= hi:
        if v != 0 and v >= lo:
            parts.append(f'<line class="grid" x1="{L}" x2="{W-R}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
                         f'<text class="tick" x="{L-8}" y="{y(v)+4:.1f}" text-anchor="end">{fmt_units(v).replace(".00","")}</text>')
        v += step
    parts.append(f'<line class="zero" x1="{L}" x2="{W-R}" y1="{y(0):.1f}" y2="{y(0):.1f}"/>'
                 f'<text class="tick" x="{L-8}" y="{y(0)+4:.1f}" text-anchor="end">0</text>')
    # a tick at the first pick of each day
    dates = done.date.tolist()
    for i, d in enumerate(dates):
        if i == 0 or d != dates[i - 1]:
            xx = x(i + 1)
            parts.append(f'<line class="day" x1="{xx:.1f}" x2="{xx:.1f}" y1="{T}" y2="{H-B}"/>'
                         f'<text class="tick" x="{xx:.1f}" y="{H-12}" text-anchor="middle">'
                         f'{nice(d, "%b")} {int(d[8:])}</text>')
    parts.append(f'<polyline class="run" points="{pts}"/>')
    for i, (v, r) in enumerate(zip(cum, done.result), start=1):
        parts.append(f'<circle class="dot {r}" cx="{x(i):.1f}" cy="{y(v):.1f}" r="4">'
                     f'<title>Pick {i}: {r}, running total {fmt_units(v)}</title></circle>')
    parts.append("</svg>")
    return "".join(parts)


def table(headers, rows, cls=""):
    th = "".join(f'<th class="num">{h}</th>' if h in NUM_HEADERS else f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(rows_cell) + "</tr>" for rows_cell in rows)
    return f'<div class="scroll"><table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def td(v, cls=""):
    return f'<td class="{cls}">{v}</td>' if cls else f"<td>{v}</td>"


def result_cell(r):
    label = {"W": "Won", "L": "Lost", "P": "Push", "pending": "Waiting"}.get(r, esc(r))
    return f'<td><span class="res {esc(r)}">{label}</span></td>'


def breakdown_rows(done, label):
    groups = []
    d = done.copy()
    d["market"] = d.bet.str.endswith(" ML").map({True: "Moneyline", False: "Totals"})
    d["side"] = d.apply(lambda r: ("Underdog" if r.odds >= 100 else "Favorite")
                        if r.market == "Moneyline" else r.bet.split()[0], axis=1)
    d["bucket"] = pd.cut(d.edge, [0, 0.05, 0.07, 1], right=False,
                         labels=["3 to 5 points", "5 to 7 points", "7+ points"])
    for col, title in (("market", "Market"), ("side", "Side"), ("bucket", "Edge size")):
        for k, g in d.groupby(col, observed=True):
            w, l, p, u, roi = record(g)
            groups.append([td(label), td(title, "muted"), td(esc(k)), td(f"{w}\u2013{l}" + (f"\u2013{p}" if p else "")),
                           td(fmt_units(u), "num " + sign_class(u)), td(fmt_roi(roi), "num " + sign_class(u)),
                           td(f"{100*g.edge.mean():.1f}", "num")])
    return groups


# ---------------------------------------------------------------- tonight's storylines
def _read(name):
    path = os.path.join(HERE, name)
    try:
        return pd.read_csv(path) if os.path.exists(path) else None
    except Exception:
        return None


def _slate():
    """Tonight's games (from odds.csv) and their date (from the newest odds_history file).
    Automated runs write slate.csv from the NHL schedule, which is used first."""
    sl = _read("slate.csv")
    if sl is not None:
        return (sl[["home", "away"]], str(sl.date.iloc[0])) if len(sl) else (None, None)
    odds = _read("odds.csv")
    if odds is None or odds.empty:
        return None, None
    hist = sorted(glob.glob(os.path.join(HERE, "odds_history", "odds_*.csv")))
    m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(hist[-1])) if hist else None
    day = m[1] if m else datetime.now().strftime("%Y-%m-%d")
    return odds, day


TWO_WORD = ("Red Wings", "Maple Leafs", "Golden Knights", "Blue Jackets")


def _short(team):
    if not team:
        return team
    return next((n for n in TWO_WORD if team.endswith(n)), team.split()[-1])


def _streaks(scores, day):
    """team -> (kind 'W'/'L', length, OT losses in the streak)."""
    rows = []
    for r in scores[scores.date < day].itertuples():
        hw = r.home_g > r.away_g
        rows += [(r.date, r.home, hw, r.ot), (r.date, r.away, not hw, r.ot)]
    out = {}
    df = pd.DataFrame(rows, columns=["date", "team", "won", "ot"]).sort_values("date")
    for team, g in df.groupby("team"):
        res = list(zip(g.won, g.ot))[::-1]
        kind, n, otl = res[0][0], 0, 0
        for won, ot in res:
            if won != kind:
                break
            n += 1
            otl += (not won) and ot in ("OT", "SO")
        out[team] = ("W" if kind else "L", n, otl)
    return out


def storylines(lines, day_lines):
    """List of (score, label, game, text). Every source is optional; missing files just skip."""
    odds, day = _slate()
    if odds is None:
        return [], None
    games = [(r.away, r.home) for r in odds.itertuples()]
    game_name = {t: f"{_short(a)} @ {_short(h)}" for a, h in games for t in (a, h)}
    teams = set(game_name)
    items = []
    scores = _read("scores.csv")
    if scores is not None:
        scores["date"] = scores.date.astype(str)

    # Streaks
    if scores is not None and len(scores):
        for team, (kind, n, otl) in _streaks(scores, day).items():
            if team in teams and n >= 4:
                text = (f"{team} have won {n} straight." if kind == "W" else
                        f"{team} have lost {n} straight" + (f" ({otl} in OT or a shootout)." if otl else "."))
                items.append((n, "Streak", game_name[team], text))

    # Rest / back-to-backs
    if scores is not None and len(scores):
        d0 = pd.Timestamp(day)
        last = {}
        for r in scores.itertuples():
            for t in (r.home, r.away):
                last[t] = max(last.get(t, ""), r.date)
        for a, h in games:
            rest = {t: (d0 - pd.Timestamp(last[t])).days - 1 if t in last and last[t] < day else None for t in (a, h)}
            tired = [t for t in (a, h) if rest[t] == 0]
            for t in tired:
                other = h if t == a else a
                extra = (f" {other} have had {rest[other]} days off." if rest[other] and rest[other] >= 2 else
                         f" So did {other}." if rest[other] == 0 else "")
                where = "on the road " if t == a else ""
                items.append((6 if t == a else 5, "Rest", game_name[t],
                              f"{t} played last night, so this is the second night of a back-to-back {where}".rstrip() + "." + extra))

    # Starting goalies
    st, gs = _read("goalies.csv"), _read("goalie_stats.csv")
    if st is not None and gs is not None and len(gs):
        gs = gs.sort_values("shots", ascending=False)
        ranked = gs[gs.shots >= 100].sort_values("saved_per_game", ascending=False).reset_index(drop=True)
        n_rank = len(ranked)
        by_key = {k: r for k, r in zip(gs.key, gs.itertuples())}
        import unicodedata
        key = lambda n: re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", str(n)).encode("ascii", "ignore").decode().lower())
        sv = lambda r: f"{r.sv_pct:.3f}".lstrip("0") if pd.notna(r.sv_pct) else "n/a"
        for r in st.itertuples():
            if (r.away, r.home) not in games:
                continue
            for team, goalie, status in ((r.away, r.away_goalie, r.away_status), (r.home, r.home_goalie, r.home_status)):
                g = by_key.get(key(goalie))
                if g is None:
                    items.append((4, "Goalie", game_name[team], f"{goalie} starts for {team}: no NHL games yet this season, so V3 treats him as league average."))
                    continue
                pos = ranked.index[ranked.key == g.key]
                if len(pos):
                    i = int(pos[0])
                    if i < 5 or i >= n_rank - 5:
                        best = i < 5
                        place = i + 1 if best else n_rank - i
                        word = "best" if best else "worst"
                        nth = {1: "", 2: "2nd-", 3: "3rd-"}.get(place, f"{place}th-")
                        items.append((4 + 20 * abs(g.saved_per_game), "Goalie", game_name[team],
                                      f"{goalie} starts for {team}: {sv(g)} save %, {g.gsax:+.1f} goals saved above expected, "
                                      f"{nth}{word} among goalies with 100+ shots."))
                no1 = gs[gs.team == team].head(1)
                if len(no1) and no1.key.iloc[0] != g.key and no1.shots.iloc[0] >= 2 * max(g.shots, 1):
                    n1 = no1.iloc[0]
                    items.append((5, "Goalie", game_name[team],
                                  f"{team} start {goalie} instead of {n1['name']} ({sv(n1)}), who has faced the most shots for them."))
                if str(status) != "Confirmed":
                    items.append((2.5, "Goalie", game_name[team], f"{team} starter {goalie} is not confirmed yet (listed as {str(status).lower()})."))

    # Over/under vs BetMGM's lines
    hist = sorted(glob.glob(os.path.join(HERE, "odds_history", "odds_*.csv")))
    if scores is not None and hist:
        oh = []
        for f in hist:
            try:
                x = pd.read_csv(f)
            except Exception:
                continue
            m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(f))
            x["date"], x["file"] = m[1], os.path.basename(f)
            oh.append(x)
        if oh:
            oh = pd.concat(oh).sort_values("file").drop_duplicates(["date", "home", "away"], keep="last")
            mg = oh.merge(scores, on=["date", "home", "away"])
            mg = mg[mg.total_line.notna()]
            mg["tot"] = mg.home_g + mg.away_g
            mg["ou"] = (mg.tot > mg.total_line).map({True: "O", False: "U"}).where(mg.tot != mg.total_line, "P")
            for team in teams:
                x = mg[(mg.home == team) | (mg.away == team)].sort_values("date")
                o, u = (x.ou == "O").sum(), (x.ou == "U").sum()
                if o + u >= 6:
                    share = max(o, u) / (o + u)
                    if share >= 0.75:
                        side = "over" if o > u else "under"
                        items.append((3 + 10 * (share - 0.5), "Over/under", game_name[team],
                                      f"{team} games have gone {side} BetMGM's total in {max(o, u)} of {o + u}."))

    # Luck: goals vs expected goals
    xg = _read("xg_games.csv")
    if xg is not None and len(xg):
        cur = xg[xg.season == xg.season.max()]
        for team in teams:
            h, a = cur[cur.home == team], cur[cur.away == team]
            if len(h) + len(a) < 8:
                continue
            luck = ((h.home_goals.sum() + a.away_goals.sum()) - (h.home_xg.sum() + a.away_xg.sum())
                    - ((h.away_goals.sum() + a.home_goals.sum()) - (h.away_xg.sum() + a.home_xg.sum())))
            if abs(luck) >= 6:
                own = team + ("'" if team.endswith("s") else "'s")
                text = (f"{own} goal difference is {luck:.0f} goals better than their expected goals suggest. That usually fades."
                        if luck > 0 else
                        f"{own} goal difference is {-luck:.0f} goals worse than their expected goals suggest. That usually improves.")
                items.append((2 + abs(luck) / 2, "Luck", game_name[team], text))

    # The models
    if day_lines is not None and len(day_lines):
        here = [m for m in MODELS if m in set(day_lines.model)]
        flagged = day_lines[day_lines.flagged]
        for (game, bet), g in flagged.groupby(["game", "bet"]):
            who = set(g.model)
            a, h = game.split(" @ ")
            short = f"{_short(a)} @ {_short(h)}"
            if len(here) >= 3 and who >= set(here):
                items.append((4, "Models", short, f"All {len(here)} versions like {bet}."))
        if "v3" in here and "xg" in here:
            e = day_lines.pivot_table(index=["game", "bet"], columns="model", values="edge", aggfunc="first")
            if "v3" in e and "xg" in e:
                e = e.dropna(subset=["v3", "xg"])
                e["diff"] = e.v3 - e.xg
                for (game, bet), r in e[(e["diff"].abs() >= 0.025) & (e[["v3", "xg"]].max(axis=1) >= 0.03)].iterrows():
                    a, h = game.split(" @ ")
                    more = "more" if r["diff"] > 0 else "less"
                    items.append((3.5 + 20 * abs(r["diff"]), "Models", f"{_short(a)} @ {_short(h)}",
                                  f"V3 likes {bet} {more} than V2 ({100*r.v3:+.1f} vs {100*r.xg:+.1f} edge) because of tonight's starting goalies."))

    # keep the most notable: at most 2 per game, 2 per type, 5 overall
    items.sort(key=lambda t: -t[0])
    out, per_game, per_type = [], {}, {}
    for it in items:
        if per_game.get(it[2], 0) < 2 and per_type.get(it[1], 0) < 2:
            out.append(it)
            per_game[it[2]] = per_game.get(it[2], 0) + 1
            per_type[it[1]] = per_type.get(it[1], 0) + 1
        if len(out) == 5:
            break
    return out, day


def storylines_html(lines):
    try:
        day_lines = None
        odds, day = _slate()
        if day is not None and len(lines):
            day_lines = lines[lines.date == day]
        items, day = storylines(lines, day_lines)
    except Exception as e:  # never let a storyline break the dashboard
        return f'<p class="empty">Storylines unavailable today ({esc(e.__class__.__name__)}).</p>', None
    if not items:
        return '<p class="empty">Nothing unusual on tonight\'s slate.</p>', day
    lis = "".join(f'<li><span class="tag">{esc(lbl)}</span><span class="sg">{esc(game)}</span>'
                  f'<span class="st">{esc(text)}</span></li>' for _, lbl, game, text in items)
    return f'<ul class="stories">{lis}</ul>', day


# ---------------------------------------------------------------- trend systems (paper)
TRACK_FROM = "2026-11-02"  # live tracking starts here; earlier games are a backtest
SYSTEMS = [
    ("fav200", "Favorites of −200 or more", "Bet the favorite when its moneyline is −200 or shorter."),
    ("homeloss", "Home team after a loss", "Bet the home team when it lost its previous game (OT/shootout losses count)."),
    ("hotover", "Over after two high-scoring games", "Bet the over when both teams' previous games had 7+ goals."),
    ("rest", "Rested home vs. tired road team", "Bet the home team when the road team played yesterday and the home team didn't."),
]


def _odds_history():
    rows = []
    for f in sorted(glob.glob(os.path.join(HERE, "odds_history", "odds_*.csv"))):
        try:
            x = pd.read_csv(f)
        except Exception:
            continue
        m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(f))
        x["date"], x["file"] = m[1], os.path.basename(f)
        rows.append(x)
    if not rows:
        return None
    return pd.concat(rows).sort_values("file").drop_duplicates(["date", "home", "away"], keep="last")


def _profit(odds):
    return odds / 100 if odds > 0 else 100 / -odds


def system_bets():
    """Every bet each system would have made, graded where a final score exists."""
    oh, scores = _odds_history(), _read("scores.csv")
    if oh is None or scores is None:
        return None
    scores["date"] = scores.date.astype(str)
    sc = scores.sort_values("date")
    played = set(zip(sc.date, sc.home)) | set(zip(sc.date, sc.away))
    hist = {}  # team -> list of (date, won, total)
    for r in sc.itertuples():
        hist.setdefault(r.home, []).append((r.date, r.home_g > r.away_g, r.home_g + r.away_g))
        hist.setdefault(r.away, []).append((r.date, r.away_g > r.home_g, r.home_g + r.away_g))
    final = {(r.date, r.home, r.away): r for r in sc.itertuples()}

    def prev(team, day):
        games = [g for g in hist.get(team, []) if g[0] < day]
        return games[-1] if games else None

    def yesterday(day):
        return (pd.Timestamp(day) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    bets = []
    for r in oh.itertuples():
        day, game = r.date, f"{_short(r.away)} @ {_short(r.home)}"
        picks = []
        if pd.notna(r.home_ml) and pd.notna(r.away_ml):
            if min(r.home_ml, r.away_ml) <= -200:
                home_fav = r.home_ml < r.away_ml
                picks.append(("fav200", f"{(r.home if home_fav else r.away)} ML", "home" if home_fav else "away",
                              r.home_ml if home_fav else r.away_ml))
            ph = prev(r.home, day)
            if ph is not None and not ph[1]:
                picks.append(("homeloss", f"{r.home} ML", "home", r.home_ml))
            if (yesterday(day), r.away) in played and (yesterday(day), r.home) not in played:
                picks.append(("rest", f"{r.home} ML", "home", r.home_ml))
        if pd.notna(r.total_line) and pd.notna(r.over):
            ph, pa = prev(r.home, day), prev(r.away, day)
            if ph is not None and pa is not None and ph[2] >= 7 and pa[2] >= 7:
                picks.append(("hotover", f"Over {r.total_line:g}", "over", r.over))
        f = final.get((day, r.home, r.away))
        for sys_, bet, side, odds in picks:
            if f is None:
                res, units = "pending", None
            elif side in ("home", "away"):
                won = (f.home_g > f.away_g) == (side == "home")
                res, units = ("W", _profit(odds)) if won else ("L", -1.0)
            else:
                tot = f.home_g + f.away_g
                res, units = (("P", 0.0) if tot == r.total_line else
                              ("W", _profit(odds)) if tot > r.total_line else ("L", -1.0))
            bets.append(dict(system=sys_, date=day, game=game, bet=bet, odds=int(odds), result=res, units=units))
    return pd.DataFrame(bets)


def systems_html():
    try:
        b = system_bets()
    except Exception as e:
        return f'<p class="empty">Trend systems unavailable ({esc(e.__class__.__name__)}).</p>'
    if b is None:
        return '<p class="empty">Trend systems need scores.csv (from grade_picks.py) and the odds_history folder.</p>'
    today = b.date.max() if len(b) else None
    rows = []
    for key_, name, rule in SYSTEMS:
        x = b[b.system == key_] if len(b) else b
        live = x[(x.date >= TRACK_FROM) & x.result.isin(["W", "L", "P"])]
        back = x[(x.date < TRACK_FROM) & x.result.isin(["W", "L", "P"])]
        lw, ll, lp, lu, lroi = record(live)
        bw, bl, bp, bu, broi = record(back)
        tonight = x[(x.date == today) & (x.result == "pending")] if today else x.iloc[0:0]
        tn = "<br>".join((f"{esc(t.bet)}, {esc(t.game)}" if t.bet.startswith("Over") else esc(t.bet))
                        + f" ({fmt_odds(t.odds)})" for t in tonight.itertuples()) or '<span class="muted">None</span>'
        rows.append([td(f"<b>{esc(name)}</b><br><span class=\"muted small\">{esc(rule)}</span>", "wrap"),
                     td(fmt_rec(lw, ll, lp)), td(fmt_units(lu), "num " + sign_class(lu)),
                     td(fmt_rec(bw, bl, bp), "muted"), td(fmt_units(bu), "num muted"),
                     td(tn, "wrap")])
    return table(["System", "Live record", "Live units", "Backtest", "Backtest units", "Tonight"], rows)


def main():
    lines = load_lines()
    if lines.empty:
        raise SystemExit("No picks_*.txt files found in this folder.")
    res = load_results()
    lines["result"], lines["units"], lines["final"] = zip(*[
        res.get((r.model, r.date, r.game, r.bet), ("pending", None, "")) if r.flagged else ("", None, "")
        for r in lines.itertuples()])
    lines["units"] = pd.to_numeric(lines["units"], errors="coerce")
    picks = lines[lines.flagged].sort_values(["date", "order"])
    graded = picks.result.isin(["W", "L", "P"])

    # ---- headline and chart for the focus version (V1 locally, V3 on the website)
    fpk = picks[picks.model == FOCUS]
    done = fpk[fpk.result.isin(["W", "L", "P"])]
    w, l, p, u, roi = record(done)
    n = len(done)
    fname = NAMES[FOCUS]
    since = nice(fpk.date.min(), "%b") + " " + str(int(fpk.date.min()[8:])) if len(fpk) else "today"
    headline = f'{fname} {fmt_rec(w, l, p)}, <span class="{sign_class(u)}">{fmt_units(u)} units</span>'
    sub = (f"V3, V2 plus tonight's starting goalies: {n} graded picks at 1 unit each since {since}, ROI {fmt_roi(roi)}. "
           "Picked automatically about an hour before each game." if FOCUS == "v3" else
           f"V1, the main model: {n} graded picks at 1 unit each since Sept 30, ROI {fmt_roi(roi)}.")
    progress = min(100, 100 * n / TARGET)
    story_html, story_day = storylines_html(lines)
    sys_html = systems_html()

    # ---- head to head since V2 started
    starts = lines.groupby("model").date.min().to_dict()  # first day each version has picks files
    xg_dates = lines.loc[lines.model == "xg", "date"]
    if xg_dates.empty:
        h2h_html = '<p class="empty">V2 picks appear here once picks_xg_DATE.txt files exist.</p>'
        h2h_start = None
    else:
        h2h_start = xg_dates.min()
        rows, daily = [], {}
        for m in MODELS:
            if m not in starts:
                continue
            pm = picks[(picks.model == m) & (picks.date >= h2h_start)]
            dm = pm[pm.result.isin(["W", "L", "P"])]
            mw, ml, mp, mu, mroi = record(dm)
            rows.append([td(f"<b>{NAMES[m]}</b> " + DESC[m]) + td(nice(max(starts[m], h2h_start), "%b") + " " + str(int(max(starts[m], h2h_start)[8:])), "muted"),
                         td(len(dm), "num"), td(fmt_rec(mw, ml, mp)),
                         td(fmt_units(mu), "num " + sign_class(mu)), td(fmt_roi(mroi), "num " + sign_class(mu)),
                         td(len(pm) - len(dm), "num")])
            daily[m] = dm.groupby("date").units.sum().cumsum().to_dict()
        h2h_html = (table(["Version", "Since", "Graded", "Record", "Units", "ROI", "Waiting"], rows)
                    + f'<div class="rink small">{h2h_svg(daily)}</div>')

    # ---- one row per bet with both models side by side
    def merged(df):
        out = []
        for (d, game, bet), g in df.groupby(["date", "game", "bet"], sort=False):
            bym = {r.model: r for r in g.itertuples()}
            first = next(bym[m] for m in MODELS if m in bym)
            who = [NAMES[m] for m in MODELS if m in bym and bym[m].flagged]
            res_row = next((bym[m] for m in MODELS if m in bym and bym[m].flagged), first)
            out.append(dict(date=d, game=game, bet=bet, order=first.order, odds=first.odds,
                            fair_p=first.fair_p,
                            **{f"e_{m}": (bym[m].edge if m in bym else None) for m in MODELS},
                            who=" + ".join(who), result=res_row.result, final=res_row.final,
                            maxedge=max(bym[m].edge for m in bym if bym[m].flagged) if who else None))
        return pd.DataFrame(out)

    wait_keys = picks[~graded][["date", "game", "bet"]].drop_duplicates()
    waiting = merged(lines.merge(wait_keys, on=["date", "game", "bet"])) if len(wait_keys) else pd.DataFrame()
    if len(waiting):
        waiting = waiting.sort_values(["date", "order"])
        wcols = [m for m in MODELS if waiting[f"e_{m}"].notna().any()]
        wait_html = table(["Date", "Game", "Bet", "Odds", "Market"] + [f"{NAMES[m]} edge" for m in wcols] + ["Picked by"], [
            [td(r.date), td(esc(r.game)), td(esc(r.bet)), td(fmt_odds(r.odds), "num"),
             td(f"{100*r.fair_p:.1f}%", "num"), *[td(fmt_edge(getattr(r, f"e_{m}")), "num") for m in wcols],
             td(esc(r.who) + (' <span class="flag" title="Big disagreement with the market. Assume the model is missing something.">check</span>'
                              if r.maxedge is not None and r.maxedge >= 0.07 else ""))]
            for r in waiting.itertuples()])
    else:
        wait_html = '<p class="empty">Nothing waiting. Run tonight\'s routine to add picks.</p>'

    # ---- day by day, both models
    daily_rows = []
    for d in sorted(picks.date.unique(), reverse=True):
        cells = [td(d)]
        for m in MODELS:
            g = picks[(picks.date == d) & (picks.model == m)]
            gd = g[g.result.isin(["W", "L", "P"])]
            if g.empty and (m not in starts or d < starts[m] or d >= RETIRED.get(m, "9999")):
                cells += [td("\u2014", "muted"), td("", "num")]
            elif gd.empty:
                cells += [td("Waiting" if len(g) else "No picks", "muted"), td("", "num")]
            else:
                dw, dl, dp, du, _ = record(gd)
                cells += [td(fmt_rec(dw, dl, dp)), td(fmt_units(du), "num " + sign_class(du))]
        daily_rows.append(cells)
    daily_html = table(["Date"] + [h for m in MODELS for h in (f"{NAMES[m]} record", f"{NAMES[m]} units")], daily_rows)

    # ---- breakdowns for each model
    brows = []
    for m in MODELS:
        dm = picks[(picks.model == m) & graded]
        if len(dm):
            brows += breakdown_rows(dm, NAMES[m])
    breakdown_html = (table(["Version", "", "Group", "Record", "Units", "ROI", "Avg edge"], brows)
                      if brows else '<p class="empty">Breakdowns appear once picks are graded.</p>')

    # ---- every line priced, newest day first, both models side by side
    days = []
    for d in sorted(lines.date.unique(), reverse=True):
        g = merged(lines[lines.date == d]).sort_values("order")
        dcols = [m for m in MODELS if g[f"e_{m}"].notna().any()]
        rows = []
        for r in g.itertuples():
            picked = bool(r.who)
            rows.append(f'<tr class="{"pick" if picked else "pass"}">'
                        + td(esc(r.game)) + td(esc(r.bet)) + td(fmt_odds(r.odds), "num")
                        + td(f"{100*r.fair_p:.1f}%", "num")
                        + "".join(td(fmt_edge(getattr(r, f"e_{m}")), "num") for m in dcols)
                        + td(esc(r.who) if picked else "Pass", "" if picked else "muted")
                        + (result_cell(r.result) if picked else td(""))
                        + td(esc(r.final), "muted") + "</tr>")
        npk = int((g.who != "").sum())
        days.append(f'<details{" open" if not days else ""}><summary>{nice(d, "%A, %B")} {int(d[8:])}'
                    f'<span class="muted"> \u2014 {g.game.nunique()} games, {npk} bets picked</span></summary>'
                    f'<div class="scroll"><table class="lines"><thead><tr><th>Game</th><th>Bet</th><th class="num">Odds</th>'
                    f'<th class="num">Market</th>{"".join(f'<th class="num">{NAMES[m]} edge</th>' for m in dcols)}<th>Picked by</th>'
                    f'<th>Result</th><th>Final</th></tr></thead>'
                    f'<tbody>{"".join(rows)}</tbody></table></div></details>')

    page = f"""<!doctype html>
<html lang="en" data-theme="dark"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NHL model paper picks</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;700&family=Barlow:wght@400;600&display=swap" rel="stylesheet">
<style>
:root {{ --ice:#EEF3F7; --board:#FFFFFF; --ink:#14243A; --soft:#5F6F82; --rule:#D3DDE6;
        --blue:#1E5AA8; --red:#C8102E; --grey:#9AA7B4; --badge:#FFFFFF; --v2:#B7791F; --v2b:#1F8A70; --v3:#7B4FC9; color-scheme:light; }}
:root[data-theme="dark"] {{ --ice:#0F1824; --board:#172231; --ink:#E6EDF4; --soft:#93A3B5; --rule:#2A3A4D;
        --blue:#6EA4EE; --red:#F0657A; --grey:#6C7B8B; --badge:#0F1824; --v2:#F6C26B; --v2b:#5FD3B0; --v3:#C3A6FF; color-scheme:dark; }}
.top {{ display:flex; justify-content:space-between; align-items:baseline; gap:12px; }}
.theme {{ font:600 14px Barlow, "Segoe UI", sans-serif; color:var(--soft); background:var(--board);
         border:1px solid var(--rule); border-radius:14px; padding:5px 12px; cursor:pointer; }}
.theme:hover {{ color:var(--ink); }}
.theme:focus-visible {{ outline:3px solid var(--blue); outline-offset:2px; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--ice); color:var(--ink);
       font:16px/1.5 Barlow, "Segoe UI", system-ui, sans-serif; }}
main {{ max-width:980px; margin:0 auto; padding:32px 20px 64px; }}
h1, h2, .big {{ font-family:"Barlow Condensed", "Arial Narrow", "Segoe UI", sans-serif; }}
h1 {{ font-size:18px; font-weight:500; color:var(--soft); margin:0 0 4px; }}
.big {{ font-size:clamp(44px, 9vw, 76px); font-weight:700; line-height:1; margin:0; letter-spacing:-0.5px; }}
.sub {{ color:var(--soft); margin:10px 0 0; max-width:60ch; }}
.pos {{ color:var(--blue); }} .neg {{ color:var(--red); }} .muted {{ color:var(--soft); }}
.rink {{ background:var(--board); border:2px solid var(--rule); border-radius:28px; padding:18px 18px 6px; margin:24px 0 10px; }}
svg {{ width:100%; height:auto; display:block; }}
.grid {{ stroke:var(--rule); stroke-width:1; }}
.zero {{ stroke:var(--red); stroke-width:3; }}
.day {{ stroke:var(--blue); stroke-width:1; stroke-dasharray:3 4; opacity:.45; }}
.run {{ fill:none; stroke:var(--ink); stroke-width:2.5; stroke-linejoin:round; }}
.dot.W {{ fill:var(--blue); }} .dot.L {{ fill:var(--red); }} .dot.P {{ fill:var(--grey); }}
.rink.small {{ border-radius:20px; margin:14px 0 0; }}
.h2h {{ fill:none; stroke-width:2.5; stroke-linejoin:round; }}
.h2h.main {{ stroke:var(--ink); }} .h2h.xg {{ stroke:var(--v2); stroke-dasharray:7 4; }}
.h2h.xgb {{ stroke:var(--v2b); stroke-dasharray:2 4; stroke-linecap:round; }}
.h2hdot.main {{ fill:var(--ink); }} .h2hdot.xg {{ fill:var(--v2); }} .h2hdot.xgb {{ fill:var(--v2b); }}
.h2h.v3 {{ stroke:var(--v3); stroke-dasharray:12 4 2 4; }} .h2hdot.v3 {{ fill:var(--v3); }}
.lbl {{ font:700 13px Barlow, "Segoe UI", sans-serif; }} .lbl.main {{ fill:var(--ink); }} .lbl.xg {{ fill:var(--v2); }} .lbl.xgb {{ fill:var(--v2b); }} .lbl.v3 {{ fill:var(--v3); }}
.tick {{ fill:var(--soft); font-size:12px; font-family:Barlow, "Segoe UI", sans-serif; }}
.goal {{ display:flex; align-items:center; gap:12px; color:var(--soft); font-size:14px; margin:6px 0 40px; }}
.bar {{ flex:1; max-width:320px; height:6px; background:var(--rule); border-radius:3px; overflow:hidden; }}
.bar span {{ display:block; height:100%; width:{progress:.1f}%; background:var(--blue); }}
h2 {{ font-size:28px; font-weight:700; margin:40px 0 4px; }}
h2 + p {{ color:var(--soft); margin:0 0 12px; max-width:65ch; }}
.scroll {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-size:14.5px; background:var(--board); }}
th {{ text-align:left; font-weight:600; color:var(--soft); border-bottom:2px solid var(--rule); padding:8px 10px; white-space:nowrap; }}
td {{ border-bottom:1px solid var(--rule); padding:7px 10px; white-space:nowrap; }}
td.num, th:nth-child(n+3) {{ font-variant-numeric:tabular-nums; }}
td.num, th.num {{ text-align:right; }}
tr.pass td {{ color:var(--soft); }}
tr.pick td {{ font-weight:600; }}
.res {{ display:inline-block; min-width:62px; text-align:center; border-radius:10px; padding:1px 8px; font-size:13px; font-weight:600; }}
.res.W {{ background:var(--blue); color:var(--badge); }} .res.L {{ background:var(--red); color:var(--badge); }}
.res.P, .res.pending {{ background:var(--rule); color:var(--ink); }}
.flag {{ margin-left:6px; font-size:12px; font-weight:600; color:var(--red); border:1px solid var(--red); border-radius:8px; padding:0 6px; }}
details {{ background:var(--board); border:1px solid var(--rule); border-radius:12px; margin:10px 0; }}
summary {{ cursor:pointer; padding:12px 14px; font-weight:600; }}
summary:focus-visible, a:focus-visible {{ outline:3px solid var(--blue); outline-offset:2px; }}
details table {{ border-top:1px solid var(--rule); }}
.empty {{ color:var(--soft); }}
td.wrap {{ white-space:normal; min-width:150px; }} .small {{ font-size:13px; }}
.stories {{ list-style:none; padding:0; margin:0; background:var(--board); border:1px solid var(--rule); border-radius:12px; }}
.stories li {{ display:grid; grid-template-columns:76px 240px 1fr; gap:14px; align-items:baseline; padding:10px 14px; border-bottom:1px solid var(--rule); }}
.stories li:last-child {{ border-bottom:0; }}
.tag {{ font-size:12px; font-weight:600; color:var(--blue); border:1px solid var(--blue); border-radius:8px; padding:0 7px; justify-self:start; white-space:nowrap; }}
.sg {{ font-weight:600; white-space:nowrap; }}
.st {{ color:var(--ink); }}
@media (max-width:640px) {{ .stories li {{ grid-template-columns:auto 1fr; }} .st {{ grid-column:1 / -1; }} }}
footer {{ margin-top:48px; color:var(--soft); font-size:14px; max-width:70ch; }}
</style></head>
<body><main>
<div class="top"><h1>NHL model paper picks</h1><button class="theme" id="theme" type="button">Switch to light mode</button></div>
<p class="big">{headline}</p>
<p class="sub">{sub}</p>
<div class="rink">{chart_svg(done)}</div>
<div class="goal"><div class="bar"><span></span></div>{n} of {TARGET} {fname} picks before judging the model</div>

<h2>Tonight's storylines</h2>
<p>The most unusual things about {"tonight's" if story_day is None else nice(story_day, "%A") + "'s"} games, picked automatically. Context only: streaks and trends like these are usually priced in, and most fade.</p>
{story_html}

<h2>Trend systems</h2>
<p>Four simple betting angles from this season's data, tracked on paper at 1 unit and BetMGM's price from your odds pull. "Live" counts only games from Nov 2 on, after the angles were chosen; "Backtest" is the earlier games they were found in, so it flatters them. Expect most trends like these to fade as the books adjust.</p>
{sys_html}

<h2>Head to head</h2>
<p>Every version on the same games and the same BetMGM odds, counted from the day V2 started{"" if h2h_start is None else " (" + nice(h2h_start, "%b") + " " + str(int(h2h_start[8:])) + ")"}. V1 rates teams on goals only; V2 uses 50% expected goals; V2b is V2 averaged 50/50 with the sportsbook's no-vig price; V3 is V2 adjusted for tonight's starting goalies (V2b and V3 started later, see "Since").{" V1, V2 and V2b stopped on Nov 12, when V3 started running automatically, so their records are frozen." if FOCUS == "v3" else ""} Each needs 100+ graded picks before the comparison means much.</p>
{h2h_html}

<h2>Waiting on results</h2>
<p>Bets any version picked that don't have a final score yet, with each version's edge. A check tag means an edge of 7+ points, which usually means the model is missing something.</p>
{wait_html}

<h2>Where it wins and loses</h2>
<p>Graded picks for each model, split by market, side and edge size. Groups this small are mostly noise.</p>
{breakdown_html}

<h2>Day by day</h2>
{daily_html}

<h2>Every line the model priced</h2>
<p>Bold rows are bets at least one model picked. Grey rows are lines every version passed on. V2b edges are already blended with the market, so they run about half the size of V2's. V3 differs from V2 only by the starting goalies.</p>
{"".join(days)}

<footer>Updated {datetime.now():%B %d, %Y at %I:%M %p}{" Eastern" if FOCUS == "v3" else ""}. Paper tracking only. Sportsbooks are hard to beat, and many good models only break even. Expected goals data: MoneyPuck.com.</footer>
</main>
<script>
(function () {{
  var root = document.documentElement, btn = document.getElementById("theme");
  function apply(t) {{
    root.setAttribute("data-theme", t);
    btn.textContent = t === "dark" ? "Switch to light mode" : "Switch to dark mode";
  }}
  var saved = null;
  try {{ saved = localStorage.getItem("nhl-dashboard-theme"); }} catch (e) {{}}
  apply(saved === "light" ? "light" : "dark");
  btn.addEventListener("click", function () {{
    var t = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    apply(t);
    try {{ localStorage.setItem("nhl-dashboard-theme", t); }} catch (e) {{}}
  }});
}})();
</script>
</body></html>"""
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"Dashboard saved to {OUT}")
    if not os.environ.get("NHL_OUT"):
        webbrowser.open("file:///" + OUT.replace("\\", "/"))


if __name__ == "__main__":
    main()
