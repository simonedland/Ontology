"""Source adapters: every source writes into the same TGO graph via deterministic keys."""
from .desktop_export import ingest as ingest_desktop_export
from .mtproto import ingest as ingest_mtproto

__all__ = ["ingest_desktop_export", "ingest_mtproto"]
