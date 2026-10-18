"""Example SOURCE plugin: bring analyst leads from a CSV file into the graph.

Use it to load any tabular input: a tip line, a spreadsheet of accounts to watch, another tool's export.

    python -m tgo run --input case --out out/case --plugin examples.csv_leads

The plugin picks up every ``leads*.csv`` under ``--input``, in both engines. Expected columns
(see ``examples/sample_data/leads.csv``):

    account,reason,analyst
    @harbor_logistics,tip 17: payment requests,A-17
    705502050,tip 18,A-17

To adapt: copy this file, rename the plugin, and change every ``TODO(adapt)``.
"""
from __future__ import annotations

import csv
from pathlib import Path

from tgo import keys as K
from tgo.plugins import source

CASE_KEY = "case:leads"  # TODO(adapt): one CaseGroup per lead list (or per row, if you prefer)


def discover(input_dir: Path) -> list[Path]:
    """Which files this source reads. Return [] if there are none: the source is then skipped."""
    return sorted(Path(input_dir).rglob("leads*.csv"))  # TODO(adapt): your file pattern


@source("csv-leads", discover=discover)  # TODO(adapt): a unique source name
def ingest_leads(g, path: Path) -> None:
    # Everything written inside the activity is attributed to this run, which shows in "explain this edge".
    with g.activity("CollectionRun", f"leads:{path.name}", source="analyst-csv", file=str(path)):
        g.node(CASE_KEY, "CaseGroup", label=f"Leads: {path.stem}")
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                account = (row.get("account") or "").strip()  # TODO(adapt): your column names
                if not account:
                    continue
                # Reuse TGO's deterministic keys so the lead merges with the same account from Telegram data.
                if account.lstrip("-").isdigit():
                    target = K.user(account)
                    g.node(target, "Account", userId=int(account))
                else:
                    # A handle names an account *at a time*: key the Username, never invent a user id.
                    target = K.username(account)
                    g.node(target, "Username", usernameValue=account.lstrip("@").lower())
                g.edge("IN_CLUSTER", target, CASE_KEY,
                       status="analyst",           # a human put it there: not observed, not inferred
                       conf=0.9,                   # TODO(adapt): how much you trust the source
                       method="tip-line",          # shows as the edge's method in the viewer and exports
                       evidence=[str(path)],
                       reason=row.get("reason"), decidedBy=f"analyst:{row.get('analyst') or 'unknown'}")
