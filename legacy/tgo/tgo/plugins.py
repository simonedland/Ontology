"""Extension points: add sources, analysis steps and exporters without editing the pipeline.

    # mypkg/leads.py
    from tgo.plugins import source, step, exporter

    @source("csv-leads", discover=lambda d: sorted(d.rglob("leads*.csv")))
    def ingest_leads(g, path):                      # write with the normal adapter API
        with g.activity("CollectionRun", f"leads:{path.name}"):
            ...g.node(...); g.edge(...)

    @step("my-scoring", after="identity")           # memory engine: fn(g, cfg) -> dict
    def my_scoring(g, cfg): ...

    @exporter("my-format")                          # fn(g, out_dir, cfg) -> summary
    def export_mine(g, out_dir, cfg): ...

Load it with ``tgo run --plugin mypkg.leads``, ``Config.plugins.modules = ["mypkg.leads"]``,
or a package entry point in group ``tgo.plugins`` (discovered offline via importlib.metadata).

Engines:
* sources work in both engines (the DuckDB engine streams each plugin source as one batch);
* steps declare ``engines``: ``"memory"`` steps get ``(g, cfg)``; ``"duckdb"`` steps get
  ``(store, cfg)`` and write via ``store.flush(graph)`` or SQL into staging; memory-only steps
  run on the bounded case subgraph when the DuckDB engine is used;
* exporters get the full graph (memory engine) or the case subgraph plus ``store=`` (DuckDB).
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

BUILTIN_STEPS = ["extract", "aggregate", "coordination", "communities", "identity"]


@dataclass
class Source:
    """A registered source adapter: ``discover(input_dir)`` + ``ingest(g, path)``."""
    name: str
    discover: Callable[[Path], list[Path]]
    ingest: Callable


@dataclass
class Step:
    """A registered analysis step anchored ``after`` a built-in or plugin step, for the given engines."""
    name: str
    fn: Callable
    after: str = "identity"
    engines: tuple[str, ...] = ("memory",)


@dataclass
class Exporter:
    """A registered exporter ``fn(g, out_dir, cfg, store=None)``."""
    name: str
    fn: Callable
    engines: tuple[str, ...] = ("memory", "duckdb")


@dataclass
class Registry:
    """Process-wide plugin registry (:data:`REGISTRY`)."""
    sources: dict[str, Source] = field(default_factory=dict)
    steps: dict[str, Step] = field(default_factory=dict)
    exporters: dict[str, Exporter] = field(default_factory=dict)
    loaded: set[str] = field(default_factory=set)


REGISTRY = Registry()


def source(name: str, discover: Callable[[Path], list[Path]]):
    """Register ``fn(g, path)`` as an ingest adapter for the files ``discover(input_dir)`` returns."""
    def deco(fn):
        REGISTRY.sources[name] = Source(name, discover, fn)
        return fn
    return deco


def step(name: str, after: str = "identity", engines: tuple[str, ...] = ("memory",)):
    """Register an analysis step run after the built-in step (or plugin step) named ``after``."""
    def deco(fn):
        REGISTRY.steps[name] = Step(name, fn, after, tuple(engines))
        return fn
    return deco


def exporter(name: str, engines: tuple[str, ...] = ("memory", "duckdb")):
    """Register ``fn(g, out_dir, cfg, **kw) -> summary`` as an additional output."""
    def deco(fn):
        REGISTRY.exporters[name] = Exporter(name, fn, tuple(engines))
        return fn
    return deco


def load(modules=(), entry_points: bool = True) -> list[str]:
    """Import plugin modules (their decorators register them). Returns the names loaded."""
    names = []
    for m in modules or ():
        if m not in REGISTRY.loaded:
            importlib.import_module(m)
            REGISTRY.loaded.add(m)
            names.append(m)
    if entry_points:
        from importlib.metadata import entry_points as eps

        for ep in eps(group="tgo.plugins"):
            if ep.value not in REGISTRY.loaded:
                ep.load()
                REGISTRY.loaded.add(ep.value)
                names.append(ep.value)
    return names


def steps_after(anchor: str, engine: str) -> list[Step]:
    """Plugin steps to run right after ``anchor`` (transitively, in registration order)."""
    out, frontier = [], [anchor]
    while frontier:
        a = frontier.pop(0)
        for s in REGISTRY.steps.values():
            if s.after == a and engine in s.engines and s not in out:
                out.append(s)
                frontier.append(s.name)
    return out


def orphan_steps(engine: str) -> list[Step]:
    """Steps whose ``after`` names nothing known: run at the end rather than silently never."""
    known = set(BUILTIN_STEPS) | set(REGISTRY.steps)
    return [s for s in REGISTRY.steps.values() if s.after not in known and engine in s.engines]
