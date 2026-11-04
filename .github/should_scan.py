"""Exit into GitHub Actions output. A newer queued run should be the one that stays up."""

from __future__ import annotations

import json
import os
import urllib.request

ACTIVE = {"queued", "in_progress", "waiting", "pending", "requested"}


def main() -> None:
    output = os.environ["GITHUB_OUTPUT"]
    run = True
    try:
        repo = os.environ["GITHUB_REPOSITORY"]
        me = int(os.environ["GITHUB_RUN_ID"])
        url = f"https://api.github.com/repos/{repo}/actions/workflows/scan.yml/runs?per_page=30"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "nba-news-scanner",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.load(response)
        newer = [
            row
            for row in data.get("workflow_runs", [])
            if row["id"] > me and row["status"] in ACTIVE
        ]
        if newer:
            print("a newer scan is waiting, so this one steps aside")
            run = False
    except Exception as exc:
        print("could not list runs, starting anyway:", type(exc).__name__)
        run = True
    with open(output, "a", encoding="utf-8") as handle:
        handle.write(f"run={'true' if run else 'false'}\n")


if __name__ == "__main__":
    main()
