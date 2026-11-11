# NBA news scanner

Watches the public [Full Court Press](https://fcp.news/?all=1) feed around the clock. When a card is about minutes or a role, it prices an over or under at DraftKings Pick6 through The Odds API and sends it to Discord or Telegram. The alert also includes what that opponent has allowed to the player's position over the last 15 games. The price at alert time and the price at tip-off are kept in `data/clv.csv`.

## Run it

The always-on copy is a public GitHub Action. One job stays awake and checks Full Court Press, including coachspeak. Outs come from the Full Court Press availability cards, checked every couple of seconds, including when this PC is off. Cards that say "sign in" still publish a public share image, and the scanner reads the quote from that. The live sheet is `data/clv.csv` in that repo: https://github.com/jriordan55/nba-news-scanner

Leave `run.bat` off while that job is active. A second copy sends the same card twice.

```bat
run.bat
```

`python scanner.py --once` does a single pass. `python scanner.py --status` prints open bets.

## Alerts

Put either a Discord webhook URL or a Telegram bot token and chat id in `.env`, then restart `run.bat`. Until one of those is set, alerts are written to `data/scanner.log` and the CSV only.

## What gets a bet

Only Development, Minutes & Usage, Gameplan, and Role & Rotation are bet. A prop is void when the player does not play, so a confirmed absence is not an under. The scanner looks for the teammate the post names as the one getting those minutes, and bets that player over. A player who is still playing on a lighter load is an under. A bigger role, a start, or more of the ball is an over.

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

`data/clv.csv` has one row per bet and is rewritten as the scanner runs.

- `line at news` and `odds at news` are saved when the alert fires. `time of odds` is that moment. `time of news` is when the card was posted.
- `closing line` and `closing price` freeze at tip-off. If the book pulls the prop, the close is the last price seen.
- `result` is the box score. `hit/loss` is graded on the line at the news. A player who does not play is `void`.
- `CLV` is in points against the closing line. Positive means your number beat the close. An over of 24.5 that closes 26.5 is `+2.00`. An under of 28.5 that closes 27.5 is `+1.00`.

Pick6 posts the standard two-way line. The line is the number that usually moves.
