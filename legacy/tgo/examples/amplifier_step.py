"""Example ANALYSIS STEP plugin (memory engine): score how widely each chat's content is re-broadcast.

For every chat it counts how many *other* chats forward its posts (``FORWARDS_FROM`` in-degree) and
how often, then writes ``amplifiedBy``, ``forwardedCount`` and ``amplifierRank`` onto the chat node.
The viewer shows these properties in the details panel, and all exports carry them.

    python -m tgo run --input case --out out/case --plugin examples.amplifier_step

On the DuckDB engine this step runs on the bounded case slice. For full-corpus numbers at millions of
messages, use the SQL twin ``examples/amplifier_sql_step.py`` instead; don't load both.

To adapt: copy this file, rename the step, and change every ``TODO(adapt)``.
"""
from __future__ import annotations

from tgo.plugins import step

NAME = "amplifiers"  # TODO(adapt): unique step name (also the summary.json key)


@step(NAME, after="aggregate", engines=("memory",))  # FORWARDS_FROM is built by the aggregate step
def score_amplifiers(g, cfg) -> dict:
    opts = (getattr(getattr(cfg, "plugins", None), "options", None) or {}).get(NAME, {})
    min_sources = int(opts.get("min_sources", 2))  # TODO(adapt): what counts as "widely amplified"

    scores = []
    for chat in g.by_label("Chat"):
        edges = [e for e in g.in_edges(chat.key, "FORWARDS_FROM") if e.src != chat.key]
        if len(edges) >= min_sources:
            scores.append((len(edges), sum(e.props.get("count", 1) for e in edges), chat.key))
    scores.sort(reverse=True)

    # Writing plain properties needs no ontology change. To add *relationships* instead, declare the new
    # type in tgo/ontology.py first (see docs/extending.md, "Add a relation type").
    with g.activity("DetectionRun", f"{NAME}:min{min_sources}", agent={
            "tool": f"tgo-example-{NAME}", "version": "0.1", "codeLicense": "MIT", "weightsLicense": "n/a"},
            min_sources=min_sources):
        for rank, (sources, total, key) in enumerate(scores, 1):
            g.node(key, amplifiedBy=sources, forwardedCount=total, amplifierRank=rank)
    return {"scored": len(scores), "top": scores[0][2] if scores else None}
