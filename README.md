# NHL model: paper picks (V3)

An NHL betting model tracked on paper. V3 rates teams from goals and expected goals,
adjusts for the starting goalies, and compares its probabilities with BetMGM's odds.

**This is paper tracking only. No bets are placed, and nothing here is betting advice.**
Sportsbooks are hard to beat, and many good models only break even.

## How it runs

A GitHub Action (`.github/workflows/nhl.yml`) runs `auto_run.py` every 20 minutes
from 6 AM to 12:40 AM US Eastern:

- **Morning:** updates scores from the NHL's public feed, grades every pick, refreshes
  MoneyPuck expected goals and goalie stats, and rebuilds the dashboard.
- **About an hour before each game:** pulls BetMGM lines (The Odds API) and starting
  goalies (Daily Faceoff), runs V3 on that game, and saves the pick to `picks_v3_DATE.txt`.
  Games starting close together share one odds pull.

The dashboard is published with GitHub Pages from the `docs` folder.

## Data credits

- Expected goals and goalie stats: [MoneyPuck.com](https://moneypuck.com) (non-commercial use)
- Scores and schedule: NHL public API
- Odds: The Odds API (BetMGM US lines)
- Starting goalies: Daily Faceoff
