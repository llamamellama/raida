"""Process environment for model libraries: cache under the data dir, offline unless allowed.

Must run before huggingface_hub or any MLX model library is imported.
"""

from __future__ import annotations

import os

from raida.config import Config


def apply_model_env(config: Config) -> None:
    hf_home = config.models_dir / "hf"
    hf_home.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(hf_home))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    if config.transcribe.allow_model_download:
        os.environ.pop("HF_HUB_OFFLINE", None)
    else:
        os.environ["HF_HUB_OFFLINE"] = "1"
