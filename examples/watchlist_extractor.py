"""Example EXTRACTOR plugin: tag every text in the case with terms from your watchlist.

It runs as an analysis step right after the built-in extractors, so it sees message text, document
text, OCR output and transcripts alike. It also shows the pattern for an **optional local AI model**:
the model is used only if it sits on local disk, otherwise a plain keyword match does the job.

    python -m tgo run --input case --out out/case --plugin examples.watchlist_extractor

Settings (``tgo.toml``)::

    [plugins.options.watchlist]
    path = "examples/sample_data/watchlist.json"   # {"term": "LABEL", ...}
    model_dir = "models/my-local-classifier"       # optional; omit for keyword matching only

To adapt: copy this file, rename the step, and change every ``TODO(adapt)``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from tgo.plugins import step

NAME = "watchlist"  # TODO(adapt): a unique step name; also the summary.json key and the options key
DEFAULT_LIST = Path(__file__).parent / "sample_data" / "watchlist.json"


def _options(cfg) -> dict:
    opts = getattr(getattr(cfg, "plugins", None), "options", None) or {}
    return opts.get(NAME, {})


def _load_model(model_dir: str | None):
    """Optional local model. Three rules keep TGO offline:

    1. load only from a local directory (never a hub id);
    2. pass the library's offline flag (``local_files_only=True`` or equivalent);
    3. return None on any problem, so the step degrades instead of failing.
    """
    if not model_dir or not Path(model_dir).is_dir():
        return None
    try:
        # TODO(adapt): your library, e.g. sentence-transformers, GLiNER, a llama.cpp binding.
        from sentence_transformers import SentenceTransformer  # type: ignore

        return SentenceTransformer(model_dir, local_files_only=True)
    except Exception:
        return None


def _texts(g):
    """(node key that owns the text, text, evidence key). TODO(adapt): add or remove text sources."""
    for n in g.by_label("Message"):
        if n.props.get("text"):
            yield n.key, n.props["text"], n.key
    for e in g.by_type("HAS_TEXT") + g.by_type("HAS_TRANSCRIPT"):
        t = g.nodes[e.dst].props.get("text")
        if t:
            yield e.src, t, e.dst


@step(NAME, after="extract", engines=("memory",))
def tag_watchlist(g, cfg) -> dict:
    opts = _options(cfg)
    terms: dict[str, str] = json.loads(Path(opts.get("path", DEFAULT_LIST)).read_text(encoding="utf-8"))
    model = _load_model(opts.get("model_dir"))
    tool = "local-model" if model else "keyword"
    patterns = {t: re.compile(rf"(?<!\w){re.escape(t)}(?!\w)", re.I) for t in terms}
    hits = 0
    # The agent dict is recorded as a ModelAgent: which tool and version ran, and under which licences.
    with g.activity("DetectionRun", f"{NAME}:{tool}", agent={
            "tool": f"tgo-example-{NAME}", "version": "0.1", "modelId": opts.get("model_dir") if model else None,
            "codeLicense": "MIT", "weightsLicense": "see model card" if model else "n/a"}, terms=len(terms)):
        for owner, text, evidence in _texts(g):
            for term, label in terms.items():
                for m in patterns[term].finditer(text):
                    ek = f"entity:{label}:{term.lower()}"
                    g.node(ek, "ExtractedEntity", name=term, entityType=label, source="watchlist")
                    g.edge("MENTIONS_ENTITY", owner, ek,
                           conf=0.8,                    # inferred edges must stay below 1.0 (rule R1)
                           method=f"watchlist:{tool}", evidence=[evidence],
                           selector=f"TextPositionSelector:{m.start()}-{m.end()}",
                           discriminator=f"{evidence}:{m.start()}")   # one edge per occurrence
                    hits += 1
                    # TODO(adapt): with a model, score `text` here and set conf from its output.
    return {"terms": len(terms), "hits": hits, "tool": tool}
