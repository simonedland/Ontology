"""Offline environment kill switches, applied on ``import tgo``."""
from __future__ import annotations

import sys

# Kill switches for libraries that phone home or fetch lazily. Must be set before
# those libraries are imported (onnxruntime reads ORT_DISABLE_TELEMETRY at init).
OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_DISABLE_UPDATE_CHECK": "1",
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
    "DO_NOT_TRACK": "1",
    "ORT_DISABLE_TELEMETRY": "1",
    "GRADIO_ANALYTICS_ENABLED": "False",
}


def harden_env() -> None:
    """Apply :data:`OFFLINE_ENV` (without overriding explicit user settings)."""
    import os

    for k, v in OFFLINE_ENV.items():
        os.environ.setdefault(k, v)
    if "onnxruntime" in sys.modules:
        try:
            sys.modules["onnxruntime"].disable_telemetry_events()
        except Exception:
            pass
