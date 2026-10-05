"""Check whether any event's displayed status (upcoming vs. past) has changed
since the last successful scheduled build of this workflow.

Theme logic (see themes/waldorfkn/layouts/partials/future-events.html and
past-event-notice.html):
    event is "past" if event_date (parsed as 00:00:00 UTC) < time.Now - 24h
  <=> event is "past" from the instant (event_date + 1 day) 00:00:00 UTC onward.

So each event has exactly one transition instant: midnight UTC on the day
after its event_date. A rebuild is needed if that instant falls in the
half-open window (last_success, now] for any event, i.e.:

    last_success < event_date + 1 day <= now

Using a window (not just "yesterday") makes this robust against a missed or
failed run: a transition is never silently skipped, it is just caught on the
next successful check.

The timestamp of the last successful scheduled run is fetched directly from
the GitHub REST API (stdlib urllib, no `gh` CLI dependency).

Required environment variables:
    GITHUB_TOKEN       token with 'actions: read' permission
    GITHUB_REPOSITORY  "owner/repo", as provided by GitHub Actions
    GITHUB_WORKFLOW_FILE  workflow filename, e.g. "hugo.yaml"

Prints a human-readable explanation to stdout. The verdict is communicated
via the exit code, not stdout, so it can be used directly in CI:
    exit 0  -> events are up to date, no rebuild needed
    exit 1  -> an event transitioned, rebuild is needed
Absence of a prior successful run (e.g. first run ever, or API error) is
treated fail-safe as "rebuild needed" (exit 1), since staleness cannot be
ruled out.
"""

import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

EVENTS_DIR = Path(__file__).resolve().parent.parent / "content" / "events"
EVENT_DATE_RE = re.compile(r"^event_date:\s*(\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)
API_BASE = "https://api.github.com"


def transition_instants() -> list[datetime.datetime]:
    """Return each event's past/upcoming transition instant, in UTC."""
    instants = []
    for md_file in EVENTS_DIR.glob("*.md"):
        text = md_file.read_text(encoding="utf-8")
        match = EVENT_DATE_RE.search(text)
        if match:
            event_date = datetime.date.fromisoformat(match.group(1))
            transition = datetime.datetime.combine(
                event_date, datetime.time.min, tzinfo=datetime.timezone.utc
            ) + datetime.timedelta(days=1)
            instants.append(transition)
    return instants


def last_successful_schedule_run() -> datetime.datetime | None:
    """Fetch the created_at timestamp of the last successful scheduled run."""
    token = os.environ.get("GITHUB_TOKEN", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    workflow_file = os.environ.get("GITHUB_WORKFLOW_FILE", "")
    if not (token and repository and workflow_file):
        print(
            "warning: missing GITHUB_TOKEN/GITHUB_REPOSITORY/GITHUB_WORKFLOW_FILE,"
            " assuming no prior run",
            file=sys.stderr,
        )
        return None

    url = (
        f"{API_BASE}/repos/{repository}/actions/workflows/{workflow_file}/runs"
        "?event=schedule&status=success&per_page=1"
    )
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.load(response)
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"warning: could not query GitHub API: {exc}", file=sys.stderr)
        return None

    runs = data.get("workflow_runs", [])
    if not runs:
        return None
    created_at = runs[0]["created_at"]  # ISO 8601, e.g. "2026-10-05T03:42:00Z"
    return datetime.datetime.fromisoformat(created_at.replace("Z", "+00:00"))


def main() -> int:
    """Return 0 if events are up to date, 1 if a rebuild is needed (exit code)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    last_success = last_successful_schedule_run()

    if last_success is None:
        print("No prior successful scheduled run found: rebuilding (fail-safe).")
        return 1

    print(f"Last successful scheduled run: {last_success.isoformat()}")
    print(f"Current time:                  {now.isoformat()}")

    due = [
        instant for instant in transition_instants() if last_success < instant <= now
    ]
    if due:
        due_str = ", ".join(sorted(instant.date().isoformat() for instant in due))
        print(f"{len(due)} event(s) transition in this window (dates: {due_str}).")
        print("Rebuild is needed.")
        return 1

    print("No event transitions in this window. Events are up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
