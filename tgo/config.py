"""All tunable knobs in one place.

Every threshold, weight and cap the pipeline uses lives here with its default
(the defaults reproduce the shipped behaviour exactly).  Override any subset
from a JSON or TOML file:

    python -m tgo config > tgo.json          # dump the defaults, edit what you need
    python -m tgo run --input case --out out --config tgo.json

or in code:

    from tgo.config import Config
    cfg = Config.load("tgo.toml")             # or Config.from_dict({...}) / Config()
    cfg.coordination.time_window = 300
    tgo.pipeline.run("case", "out", config=cfg)

Unknown keys raise an error, so a typo never silently falls back to a default.
Environment-level settings (namespace domain, local model paths, offline
hardening) stay environment variables because they are read at import time;
see ``ENVIRONMENT`` below.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


@dataclass
class CoordinationConfig:
    """CooRTweet-style coordination detection (``tgo.analysis.coordination``)."""

    time_window: int = 60            # seconds within which two shares of the same object count as co-sharing
    min_participation: int = 2       # distinct shared objects a pair needs before it is considered
    max_p: float | None = 1e-3       # Poisson null-model significance cut-off; None = plain CooRTweet (no test)
    sync_seconds: int = 5            # min delta at/below which IDENTICAL_POST pairs also get SYNC_POST
    min_text_len: int = 40           # normalised characters before text is used as a shared object
    simhash_max_dist: int = 3        # Hamming distance (64-bit SimHash) for NEAR_DUP_POST
    min_cluster_size: int = 3        # accounts needed for a CoordinationCluster node
    same_day_min: int = 3            # chats created on one day needed for SAME_CREATION_DATE edges
    same_day_max: int = 50           # ...and the upper bound (avoids cliques on bulk imports)
    max_object_accounts: int = 1000  # objects shared by more accounts are dropped before pairing (fan-out cap, both engines)


@dataclass
class CommunityConfig:
    """Seeded multi-run Louvain with stability (``tgo.analysis.communities``)."""

    seeds: int = 10                  # Louvain runs; stability = mean best-match Jaccard vs run 0
    resolution: float = 1.0          # Louvain resolution (higher = smaller communities)
    min_size: int = 3                # smallest community promoted to a CommunityCluster node
    min_stability: float = 0.6       # clusters below this are rejected as unstable
    deterministic: bool = True       # duckdb engine: single-threaded NetworKit PLM so reruns give identical clusters
    edge_weights: dict[str, float] = field(default_factory=lambda: {
        "FORWARDS_FROM": 1.0, "IDENTICAL_POST": 1.0, "SYNC_POST": 1.0, "CO_FORWARD": 0.8,
        "NEAR_DUP_POST": 0.6, "CO_URL": 0.5, "CO_FINGERPRINT": 0.5, "MEMBER_OF_COMMUNITY": 0.7,
        "LINKED_DISCUSSION": 0.7, "POSTED_IN": 0.3, "GAVE_GIFT_TO": 0.4, "REPOSTED": 0.4,
        "FEATURES": 0.6, "INVITED_BY": 0.3})   # relationship type -> weight in the actor graph


@dataclass
class IdentityConfig:
    """Candidate scoring and judgements (``tgo.analysis.identity``)."""

    min_score: float = 0.3           # noisy-OR score needed to write a CANDIDATE_SAME_PERSON edge
    auto_accept: float | None = None  # if set, candidates >= this are auto-positive (logged as a policy)
    weights: dict[str, float] = field(default_factory=lambda: {
        "same_phone": 0.9, "same_personal_channel": 0.7, "shared_device_serial": 0.6,
        "shared_identifier": 0.45, "username_history": 0.4, "same_bio_text": 0.35,
        "shared_avatar_hash": 0.35})     # evidence feature -> weight (non-biometric only)
    strong_identifiers: list[str] = field(default_factory=lambda: ["CryptoAddress", "IBAN", "PhoneNumber", "Email"])
    min_bio_len: int = 15            # shorter bios are too generic to count as shared evidence
    max_evidence_accounts: int = 50  # evidence keys shared by more accounts are ignored (fan-out cap, both engines)


@dataclass
class ExtractConfig:
    """Extractors (``tgo.extractors``)."""

    gazetteer_path: str | None = None  # JSON {"surface form": "LOC|ORG|PER"} replacing the demo gazetteer
    phash_threshold: int | None = None  # override near-duplicate Hamming threshold (default 10 pHash / 31 PDQ)


@dataclass
class ViewerConfig:
    """report.html and layouts (``tgo.viz.report``, ``tgo.pipeline.layout``)."""

    html: bool = True                # write report.html at all
    title: str = "TGO investigation view"
    max_nodes: int = 6000            # nodes embedded in the viewer (actors + evidence first); rest stays in graph.json
    layout_max_full: int = 2500      # above this node count only the actor skeleton is force-laid-out
    layout_seed: int = 7
    dashboard: bool = False          # also write dashboard.html (interactive overview, tgo.dashboard) after each run
    dashboard_max_nodes: int = 500   # actors in the dashboard relation network (highest weighted degree first)


@dataclass
class ScaleConfig:
    """Engine selection and the DuckDB corpus engine (``tgo.scale``) for millions of entries."""

    engine: str = "auto"             # memory | duckdb | auto (duckdb when input exceeds auto_engine_bytes)
    auto_engine_bytes: int = 200_000_000  # input size above which "auto" picks the duckdb engine
    batch_size: int = 50_000         # messages per streamed ingest batch (memory stays flat)
    workers: int = -1                # ingest/extract processes: -1 = cpu_count - 1, 0/1 = in-process
    threads: int | None = None       # DuckDB threads (None = all cores)
    memory_limit: str | None = None  # DuckDB memory limit, e.g. "16GB" (spills to temp_directory beyond it)
    temp_directory: str | None = None  # DuckDB spill directory
    store_path: str | None = None    # DuckDB file (default <out>/tgo.duckdb)
    neardup_exact_limit: int = 2_000_000  # distinct fingerprints up to which near-dup search is exact (Faiss flat)
    exports: list[str] = field(default_factory=lambda: ["parquet", "neo4j", "ntriples"])  # bulk exports to write (n-triples is the slowest at 10M+)
    restrict_files: bool = True      # DuckDB may only touch input/out/db/temp dirs (external access off); network is always off


@dataclass
class PluginConfig:
    """Extensions (``tgo.plugins``): extra sources, analysis steps and exporters."""

    modules: list[str] = field(default_factory=list)  # Python modules to import (their decorators register)
    entry_points: bool = True        # also load installed packages exposing entry-point group "tgo.plugins"
    options: dict[str, dict] = field(default_factory=dict)  # per-plugin settings keyed by plugin name; free-form (not validated by TGO)


@dataclass
class Config:
    """All tunables, grouped by section; build with ``Config()``, ``Config.load(path)`` or ``Config.from_dict``."""
    coordination: CoordinationConfig = field(default_factory=CoordinationConfig)
    communities: CommunityConfig = field(default_factory=CommunityConfig)
    identity: IdentityConfig = field(default_factory=IdentityConfig)
    extract: ExtractConfig = field(default_factory=ExtractConfig)
    viewer: ViewerConfig = field(default_factory=ViewerConfig)
    scale: ScaleConfig = field(default_factory=ScaleConfig)
    plugins: PluginConfig = field(default_factory=PluginConfig)

    # ---------------------------------------------------------------- I/O
    @classmethod
    def from_dict(cls, data: dict | None) -> "Config":
        """Build from a (partial) nested dict; unspecified fields keep their defaults."""
        cfg = cls()
        for section, values in (data or {}).items():
            if section not in {f.name for f in fields(cls)}:
                raise ValueError(f"unknown config section '{section}'")
            target = getattr(cfg, section)
            for k, v in (values or {}).items():
                if k not in {f.name for f in fields(target)}:
                    raise ValueError(f"unknown config key '{section}.{k}'")
                if isinstance(getattr(target, k), dict) and isinstance(v, dict):
                    merged = dict(getattr(target, k))
                    merged.update(v)          # partial override of weight tables
                    v = merged
                setattr(target, k, v)
        return cfg

    @classmethod
    def load(cls, path: str | Path | None = None, env: bool = True) -> "Config":
        """Defaults <- ``.json``/``.toml`` file (optional) <- ``TGO_<SECTION>_<FIELD>`` environment variables.

        Env values are parsed as JSON when possible (``8``, ``true``, ``["a"]``) else used as strings,
        e.g. ``TGO_SCALE_WORKERS=4``, ``TGO_SCALE_MEMORY_LIMIT=6GB`` (handy in containers).
        """
        data: dict = {}
        if path is not None:
            p = Path(path)
            if p.suffix.lower() == ".toml":
                import tomllib

                data = tomllib.loads(p.read_text(encoding="utf-8"))
            else:
                data = json.loads(p.read_text(encoding="utf-8"))
        if env:
            import os

            sections = {f.name for f in fields(cls)}
            for k, v in os.environ.items():
                if not k.startswith("TGO_"):
                    continue
                for sec in sections:
                    prefix = f"TGO_{sec.upper()}_"
                    if k.startswith(prefix):
                        try:
                            val = json.loads(v)
                        except ValueError:
                            val = v
                        data.setdefault(sec, {})[k[len(prefix):].lower()] = val
        return cls.from_dict(data)

    def to_dict(self) -> dict:
        """Nested plain-dict form (JSON-serialisable)."""
        return asdict(self)

    def dumps(self) -> str:
        """Pretty JSON of the full configuration (what ``tgo config`` prints)."""
        return json.dumps(self.to_dict(), indent=2)


# Settings that are environment variables (read at import / call time, not via Config).
ENVIRONMENT = {
    "TGO_<SECTION>_<FIELD>": "override any Config field, e.g. TGO_SCALE_WORKERS=4 (applied by Config.load)",
    "TGO_DOMAIN": "namespace host for tg:/tgr: IRIs (default tgo.example.org)",
    "TGO_WHISPER_MODEL": "local faster-whisper (CTranslate2) model dir; enables real ASR",
    "TGO_OCR_MODELS": "local dir with RapidOCR det/cls/rec .onnx files; enables real OCR",
    "TG_API_ID / TG_API_HASH / TG_SESSION": "Telethon credentials for `tgo collect` (the only online step)",
    "HF_HUB_OFFLINE, TRANSFORMERS_OFFLINE, ORT_DISABLE_TELEMETRY, DO_NOT_TRACK": "set automatically by `import tgo`",
}

__all__ = ["Config", "CoordinationConfig", "CommunityConfig", "IdentityConfig", "ExtractConfig", "ViewerConfig", "ScaleConfig", "PluginConfig",
           "ENVIRONMENT"]
