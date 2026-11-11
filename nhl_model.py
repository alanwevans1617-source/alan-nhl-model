#!/usr/bin/env python3
"""
NHL model v1.3
Regularized Poisson attack/defense ratings -> win / total / puck-line probabilities,
then a comparison against sportsbook odds.

  python nhl_model.py backtest                    # walk-forward test on last season
  python nhl_model.py backtest --xg-file xg_games.csv   # test goals vs expected goals
  python nhl_model.py predict --odds odds.csv     # price games vs. your odds
  python nhl_model.py predict --odds odds.csv --xg-file xg_games.csv --xg-weight 0.5
  python nhl_model.py predict --odds odds.csv --xg-file xg_games.csv --market-weight 0.5 --min-edge 0.02

Data: Hockey-Reference schedule pages (URL or a saved CSV path).
Optional: xg_games.csv from get_xg.py (MoneyPuck expected goals). With --xg-file, the
ratings are trained on a blend of actual goals and expected goals:
    target = xg_weight * xG + (1 - xg_weight) * goals
xg_weight 0 = goals only (the original model), 1 = expected goals only.
Optional (V3): --goalies goalies.csv --goalie-stats goalie_stats.csv adjusts each
team's expected goals against for tonight's starting goalie (from get_goalies.py),
using this season's shrunk GSAx (from get_goalie_stats.py). See goalie_adjust().
Optional: --market-weight w blends the model with the sportsbook's no-vig price
before computing the edge:  p = (1 - w) * model + w * market.  This is "V2b".
It shrinks the big, overconfident edges (the ones that have been losing).
odds.csv columns: home,away,home_ml,away_ml[,total_line,over,under]  (American odds,
team names exactly as Hockey-Reference spells them, e.g. "Utah Mammoth").
"""
import argparse
import io
import sys

import numpy as np
import pandas as pd
from scipy.stats import poisson
from sklearn.linear_model import PoissonRegressor

PREV_URL = "https://www.hockey-reference.com/leagues/NHL_2026_games.html"
CURR_URL = "https://www.hockey-reference.com/leagues/NHL_2027_games.html"


# ---------------------------------------------------------------- data
def load_games(src):
    """Regular-season games with scores. Shootout games are scored as ties."""
    if str(src).startswith("http"):
        import requests
        r = requests.get(src, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        r.raise_for_status()
        raw = pd.read_html(io.StringIO(r.text))[0]  # first table = regular season
    else:
        raw = pd.read_csv(src)
    c = raw.shape[1]
    empty = pd.Series("", index=raw.index)
    ot = raw.iloc[:, 6].fillna("").astype(str).str.strip() if c > 6 else empty
    notes = raw.iloc[:, 9].fillna("").astype(str) if c > 9 else empty
    df = pd.DataFrame({
        "date": pd.to_datetime(raw.iloc[:, 0], errors="coerce"),
        "away": raw.iloc[:, 2],
        "away_g": pd.to_numeric(raw.iloc[:, 3], errors="coerce"),
        "home": raw.iloc[:, 4],
        "home_g": pd.to_numeric(raw.iloc[:, 5], errors="coerce"),
        "ot": ot,
        "neutral": notes.str.startswith("at "),  # Stockholm, stadium games, etc.
    })
    df = df.dropna(subset=["date", "away_g", "home_g"]).copy()
    df["home_win"] = (df.home_g > df.away_g).astype(int)  # true winner, SO included
    so = df.ot.eq("SO")
    df.loc[so & (df.home_win == 1), "home_g"] -= 1  # remove the shootout bonus goal
    df.loc[so & (df.home_win == 0), "away_g"] -= 1
    return df.sort_values("date").reset_index(drop=True)


def attach_xg(g, path, weight, quiet=False):
    """Add training targets home_t/away_t = weight*xG + (1-weight)*goals.
    Games without xG data keep their actual goals."""
    xg = pd.read_csv(path)
    xg["date"] = pd.to_datetime(xg["date"])
    xg = xg.drop_duplicates(["date", "home", "away"])[["date", "home", "away", "home_xg", "away_xg"]]
    g = g.copy()
    g["home_xg"], g["away_xg"] = np.nan, np.nan
    for shift in (0, -1, 1):  # allow a one-day date difference between the sources
        need = g.home_xg.isna()
        if not need.any():
            break
        cand = xg.copy()
        cand["date"] = cand["date"] + pd.Timedelta(days=shift)
        m = g.loc[need, ["date", "home", "away"]].reset_index().merge(
            cand, on=["date", "home", "away"], how="inner").set_index("index")
        g.loc[m.index, "home_xg"] = m.home_xg
        g.loc[m.index, "away_xg"] = m.away_xg
    has = g.home_xg.notna()
    g["home_t"] = np.where(has, weight * g.home_xg + (1 - weight) * g.home_g, g.home_g)
    g["away_t"] = np.where(has, weight * g.away_xg + (1 - weight) * g.away_g, g.away_g)
    if not quiet:
        print(f"xG found for {has.sum()} of {len(g)} games (xg-weight {weight}).", file=sys.stderr)
        if has.mean() < 0.9:
            print("Warning: under 90% of games matched; check team names or rerun get_xg.py.",
                  file=sys.stderr)
    return g


# ---------------------------------------------------------------- model
class Ratings:
    """log(goals) = intercept + attack[scorer] + defense[opponent] + home_ice."""

    def __init__(self, alpha=0.01, half_life=120):
        self.alpha, self.half_life = alpha, half_life

    def _X(self, scorer, opp, home):
        scorer, opp = np.asarray(scorer), np.asarray(opp)
        X = np.zeros((len(scorer), 2 * self.n + 1))
        X[np.arange(len(scorer)), scorer] = 1
        X[np.arange(len(scorer)), self.n + opp] = 1
        X[:, -1] = home
        return X

    def fit(self, g, asof=None):
        self.teams = sorted(set(g.home) | set(g.away))
        self.ix = {t: i for i, t in enumerate(self.teams)}
        self.n = len(self.teams)
        h = g.home.map(self.ix).to_numpy()
        a = g.away.map(self.ix).to_numpy()
        home_flag = (~g.neutral).astype(float).to_numpy()
        X = np.vstack([self._X(h, a, home_flag), self._X(a, h, np.zeros(len(g)))])
        hy = g.home_t if "home_t" in g else g.home_g  # blended target when xG is used
        ay = g.away_t if "away_t" in g else g.away_g
        y = np.concatenate([hy.to_numpy(), ay.to_numpy()])
        asof = pd.Timestamp(asof) if asof is not None else g.date.max()
        age = (asof - g.date).dt.days.clip(lower=0).to_numpy()
        w = 0.5 ** (age / self.half_life) if self.half_life else np.ones(len(g))
        self.reg = PoissonRegressor(alpha=self.alpha, max_iter=1000)
        self.reg.fit(X, y, sample_weight=np.concatenate([w, w]))
        return self

    def lambdas(self, home, away, neutral=False):
        h, a = self.ix[home], self.ix[away]
        X = np.vstack([self._X([h], [a], [0.0 if neutral else 1.0]),
                       self._X([a], [h], [0.0])])
        return self.reg.predict(X)  # [home expected goals, away expected goals]


def market_probs(lh, la, total_line=None, k=15):
    """Independent-Poisson score grid -> moneyline / puck line / total probabilities.
    A tie after OT means a shootout, treated as a coin flip."""
    g = np.outer(poisson.pmf(np.arange(k), lh), poisson.pmf(np.arange(k), la))
    g /= g.sum()
    i, j = np.indices(g.shape)
    tie = g[i == j].sum()
    out = {"p_home": g[i > j].sum() + tie / 2, "p_away": g[i < j].sum() + tie / 2,
           "p_home_pl": g[i - j >= 2].sum(), "exp_total": lh + la}
    if total_line is not None:
        over, under = g[i + j > total_line].sum(), g[i + j < total_line].sum()
        out["p_over"] = over / (over + under)  # conditional on no push
    return out


# ---------------------------------------------------------------- goalies (V3)
def _key(name):
    import unicodedata, re
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s.lower())


def load_goalies(starters_path, stats_path):
    """Tonight's starters keyed by (away, home), plus per-goalie and per-team goals saved per game."""
    st = pd.read_csv(starters_path)
    gs = pd.read_csv(stats_path)
    by_name = {}
    for r in gs.itertuples():  # a goalie traded mid-season appears once per team; keep the bigger sample
        if r.key not in by_name or r.shots > by_name[r.key].shots:
            by_name[r.key] = r
    team_avg = {}
    for t, g in gs.groupby("team"):
        team_avg[t] = (g.saved_per_game * g.shots).sum() / g.shots.sum() if g.shots.sum() else 0.0
    starters = {(r.away, r.home): r for r in st.itertuples()}
    return starters, by_name, team_avg


def goalie_adjust(team, goalie, by_name, team_avg, cap=0.5):
    """Change in goals AGAINST `team` for starting `goalie`, in goals per game.

    V2's ratings are trained on 50% expected goals (no goalie in it) + 50% real goals
    (which include the team's goaltending so far), so the team's defense already holds
    about half of its season-average goaltending. Swap that half out, put tonight's
    starter in:   change = 0.5 * team_average - starter."""
    g = by_name.get(_key(goalie))
    starter = g.saved_per_game if g is not None else 0.0  # unknown / no games = league average
    change = 0.5 * team_avg.get(team, 0.0) - starter
    return max(-cap, min(cap, change)), (g is not None)


# ---------------------------------------------------------------- odds math
def implied(o):
    o = float(o)
    return 100 / (o + 100) if o > 0 else -o / (-o + 100)


def decimal(o):
    o = float(o)
    return 1 + o / 100 if o > 0 else 1 + 100 / -o


def price(p, odds, other_odds, min_edge, kelly_frac=0.25, cap=0.02, market_weight=0.0):
    q, qo = implied(odds), implied(other_odds)
    fair = q / (q + qo)  # remove the vig
    p = (1 - market_weight) * p + market_weight * fair  # blend toward the market (V2b)
    d = decimal(odds)
    ev = p * d - 1
    edge = p - fair
    kelly = max(0.0, (p * d - 1) / (d - 1))
    stake = min(cap, kelly * kelly_frac) if edge >= min_edge else 0.0
    return dict(model_p=round(p, 3), fair_p=round(fair, 3), edge=round(edge, 3),
                ev_per_unit=round(ev, 3), stake_pct=round(100 * stake, 2))


# ---------------------------------------------------------------- backtest
def walk_forward(g, alpha, half_life, burn_days=75):
    """Refit weekly using only past games; predict the following week."""
    start = g.date.min() + pd.Timedelta(days=burn_days)
    recs = []
    for w0 in pd.date_range(start, g.date.max(), freq="7D"):
        train = g[g.date < w0]
        test = g[(g.date >= w0) & (g.date < w0 + pd.Timedelta(days=7))]
        if test.empty:
            continue
        m = Ratings(alpha, half_life).fit(train, asof=w0)
        base = train.home_win.mean()
        for r in test.itertuples():
            if r.home in m.ix and r.away in m.ix:
                lh, la = m.lambdas(r.home, r.away, r.neutral)
                recs.append((market_probs(lh, la)["p_home"], base, r.home_win))
    p, b, y = (np.array(x) for x in zip(*recs))
    ll = lambda q: -np.mean(y * np.log(q) + (1 - y) * np.log(1 - q))
    return {"n": len(y), "logloss": ll(p), "baseline": ll(b),
            "accuracy": np.mean((p > 0.5) == y)}, p, y


def run_backtest(args):
    g = load_games(args.prev)
    print(f"{len(g)} games loaded; home win rate {g.home_win.mean():.3f}")
    if args.xg_file:
        print("\nGoals vs expected goals (alpha 0.03, half-life 90; lower logloss is better):")
        rows = []
        for w in (0.0, 0.25, 0.5, 0.75, 1.0):
            gw = attach_xg(g, args.xg_file, w, quiet=(w > 0))
            res, _, _ = walk_forward(gw, 0.03, 90)
            rows.append({"xg_weight": w, **res})
        print(pd.DataFrame(rows).round(4).to_string(index=False))
        print("\nxg_weight 0 = the current goals-only model. Differences under ~0.002 are noise.")
        return
    rows, best = [], None
    for alpha in (0.01, 0.03, 0.1, 0.3, 1.0):
        for hl in (None, 90, 180):
            res, p, y = walk_forward(g, alpha, hl)
            rows.append({"alpha": alpha, "half_life": hl, **res})
            if best is None or res["logloss"] < best[0]["logloss"]:
                best = (res, p, y, alpha, hl)
    print(pd.DataFrame(rows).round(4).to_string(index=False))
    res, p, y, alpha, hl = best
    print(f"\nBest: alpha={alpha}, half_life={hl}. Lower logloss than 'baseline' = real signal.")
    cal = pd.DataFrame({"p": p, "y": y}).groupby(pd.qcut(p, 5, duplicates="drop"), observed=True)
    print("\nCalibration (predicted vs actual home win rate):")
    print(cal.agg(predicted=("p", "mean"), actual=("y", "mean"), n=("y", "size")).round(3))


# ---------------------------------------------------------------- predict
def run_predict(args):
    g = pd.concat([load_games(args.prev), load_games(args.curr)], ignore_index=True)
    if args.xg_file:
        g = attach_xg(g, args.xg_file, args.xg_weight)
    m = Ratings(args.alpha, args.half_life).fit(g, asof=pd.Timestamp.today())
    odds = pd.read_csv(args.odds)
    goalie_lines = []
    if args.goalies:
        if not args.goalie_stats:
            sys.exit("--goalies needs --goalie-stats goalie_stats.csv (from get_goalie_stats.py).")
        starters, by_name, team_avg = load_goalies(args.goalies, args.goalie_stats)
    out = []
    for r in odds.itertuples():
        if r.home not in m.ix or r.away not in m.ix:
            print(f"Skipping {r.away} @ {r.home}: team name not found in data")
            continue
        lh, la = m.lambdas(r.home, r.away)
        if args.goalies:
            s = starters.get((r.away, r.home))
            if s is None:
                goalie_lines.append(f"  {r.away} at {r.home}: no starters found in goalies.csv, no adjustment")
            else:
                d_home, k_home = goalie_adjust(r.home, s.home_goalie, by_name, team_avg)  # goals against home
                d_away, k_away = goalie_adjust(r.away, s.away_goalie, by_name, team_avg)  # goals against away
                la, lh = max(0.5, la + d_home), max(0.5, lh + d_away)
                flag = lambda st: "" if str(st) == "Confirmed" else f", {st}"
                goalie_lines.append(
                    f"  {r.away} at {r.home}: {s.away_goalie}{flag(s.away_status)}{'' if k_away else ', no stats'} "
                    f"({-d_away:+.2f} goals/gm) vs {s.home_goalie}{flag(s.home_status)}{'' if k_home else ', no stats'} "
                    f"({-d_home:+.2f} goals/gm); expected total {lh + la:.2f}")
        tl = getattr(r, "total_line", np.nan)
        pr = market_probs(lh, la, None if pd.isna(tl) else tl)
        game = f"{r.away} @ {r.home}"
        out.append({"game": game, "bet": f"{r.home} ML", "odds": r.home_ml,
                    **price(pr["p_home"], r.home_ml, r.away_ml, args.min_edge, market_weight=args.market_weight)})
        out.append({"game": game, "bet": f"{r.away} ML", "odds": r.away_ml,
                    **price(pr["p_away"], r.away_ml, r.home_ml, args.min_edge, market_weight=args.market_weight)})
        if not pd.isna(tl):
            out.append({"game": game, "bet": f"Over {tl}", "odds": r.over,
                        **price(pr["p_over"], r.over, r.under, args.min_edge, market_weight=args.market_weight)})
            out.append({"game": game, "bet": f"Under {tl}", "odds": r.under,
                        **price(1 - pr["p_over"], r.under, r.over, args.min_edge, market_weight=args.market_weight)})
    res = pd.DataFrame(out)
    print(res.to_string(index=False))
    print("\nstake_pct > 0 means edge >= min-edge (quarter Kelly, capped at 2% of bankroll).")
    if args.xg_file:
        print(f"Ratings trained on {args.xg_weight:.0%} expected goals (Data: MoneyPuck.com).")
    if goalie_lines:
        print("\nStarting goalie adjustment (+ = fewer goals expected against his team than V2 assumes):")
        print("\n".join(goalie_lines))
        print("Goalie stats: this season only, shrunk toward league average (Data: MoneyPuck.com).")
    if args.market_weight:
        print(f"model_p is blended {1-args.market_weight:.0%} model / {args.market_weight:.0%} market (no-vig); "
              f"min edge {args.min_edge:.0%}.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backtest")
    b.add_argument("--prev", default=PREV_URL)
    b.add_argument("--xg-file", default=None)
    p = sub.add_parser("predict")
    p.add_argument("--odds", required=True)
    p.add_argument("--prev", default=PREV_URL)
    p.add_argument("--curr", default=CURR_URL)
    p.add_argument("--alpha", type=float, default=0.01)
    p.add_argument("--half-life", type=float, default=120)
    p.add_argument("--min-edge", type=float, default=0.03)
    p.add_argument("--xg-file", default=None)
    p.add_argument("--xg-weight", type=float, default=0.5)
    p.add_argument("--market-weight", type=float, default=0.0)
    p.add_argument("--goalies", default=None, help="goalies.csv from get_goalies.py (V3)")
    p.add_argument("--goalie-stats", default=None, help="goalie_stats.csv from get_goalie_stats.py (V3)")
    args = ap.parse_args()
    run_backtest(args) if args.cmd == "backtest" else run_predict(args)


if __name__ == "__main__":
    main()
