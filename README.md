# NBA news scanner

Watches the public [Full Court Press](https://fcp.news/?all=1) feed around the clock. When a card is about minutes or a role, it prices an over or under at PrizePicks through The Odds API and sends it to Discord or Telegram. The price at alert time and the price at tip-off are kept in `data/clv.csv`.

## Run it

The always-on copy is a private GitHub Action. One job stays awake and checks Full Court Press and the ESPN injury report about every 20 seconds, including when this PC is off. The live sheet is `data/clv.csv` in that repo: https://github.com/jriordan55/nba-news-scanner

Leave `run.bat` off while that job is active. A second copy sends the same card twice.

```bat
run.bat
```

`python scanner.py --once` does a single pass. `python scanner.py --status` prints open bets.

## Alerts

Put either a Discord webhook URL or a Telegram bot token and chat id in `.env`, then restart `run.bat`. Until one of those is set, alerts are written to `data/scanner.log` and the CSV only.

## What gets a bet

PrizePicks voids a prop when the player does not play, so a confirmed absence is not an under. The scanner looks for the teammate the post names as the one getting those minutes, and bets that player over. A player who is still playing on a lighter load is an under. A bigger role, a start, or more of the ball is an over.

Questionable tags, undecided rotations, and contract notes are ignored.

`absence_history.json` is last regular season at DraftKings, for players who are still on that same team. When someone sits, the alert uses the teammate and the market that actually cleared (points, rebounds, assists, threes, or PRA) at least 56% of the time over 8 or more games. A lighter-minute note becomes an under only when that player's own short games stayed under. Anyone who changed teams this year is left out of that file.

When a post says someone is out and does not name a replacement, add the teammate in `beneficiaries.json`:

```json
{
  "players": {
    "Chet Holmgren": ["Isaiah Hartenstein"]
  }
}
```

Some cards on the site are behind the free-account wall. The scanner only reads the takeaway and quote the page shows without signing in.

## CLV file

`data/clv.csv` has one row per bet.

- `alert_line` / `alert_price` are saved when the alert fires.
- `latest_line` / `latest_price` move until tip-off.
- `close_line` / `close_price` freeze at tip-off. `close_status` becomes `closed`, or `pulled` if the book takes the prop down. A pulled row uses the last price seen.
- `line_clv` is in points. Positive means your number was better than the comparison line. An over of 24.5 that closes 26.5 is `+2.00`. An under of 28.5 that closes 27.5 is `+1.00`.
- `price_clv_pct_points` is the close's implied probability minus the alert's, in percentage points. Positive means your side got shorter.
- `clv_basis` is `latest` before tip-off and `close` after.

PrizePicks often posts both sides at -137, so the line is the number that usually moves.
