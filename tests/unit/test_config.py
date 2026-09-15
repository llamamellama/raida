from pathlib import Path

import pytest

from raida.config import ConfigError, load_config


def test_model_is_required(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"llm\.model"):
        load_config(None, {"RAIDA_PATHS__DATA_DIR": str(tmp_path)})


def test_env_overrides_and_json_parsing(tmp_path: Path) -> None:
    cfg = load_config(
        None,
        {
            "RAIDA_LLM__MODEL": "m",
            "RAIDA_SERVER__PORT": "9000",
            "RAIDA_OCR__LANGUAGES": '["de-DE", "en-US"]',
            "RAIDA_PATHS__DATA_DIR": str(tmp_path),
        },
    )
    assert cfg.server.port == 9000
    assert cfg.ocr.languages == ["de-DE", "en-US"]
    assert cfg.paths.data_dir == tmp_path
    assert cfg.llm.num_ctx == 64000 + 8192 + 2048


def test_toml_file_and_unknown_key_rejected(tmp_path: Path) -> None:
    path = tmp_path / "raida.toml"
    path.write_text('[llm]\nmodel = "x"\nbogus = 1\n')
    with pytest.raises(ConfigError, match="bogus"):
        load_config(path, {})
    path.write_text('[llm]\nmodel = "x"\n[server]\nport = 1234\n')
    assert load_config(path, {}).server.port == 1234


def test_missing_explicit_file_fails(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml", {})
