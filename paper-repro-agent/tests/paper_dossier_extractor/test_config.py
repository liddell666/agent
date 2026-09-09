import pytest
from pydantic import ValidationError

from paper_dossier_extractor.config import Settings


def test_settings_pin_approved_limits() -> None:
    settings = Settings(api_token="x" * 32)
    assert settings.ollama_base_url == "http://ollama:11434"
    assert settings.ollama_model == "qwen3:8b"
    assert settings.ollama_keep_alive == "1s"
    assert settings.num_ctx == 16_384
    assert settings.num_predict == 1_536
    assert settings.max_chunk_source_bytes == 8_192
    assert settings.max_pages_per_chunk == 4
    assert settings.max_candidate_pages == 32
    assert settings.max_ollama_calls == 12
    assert settings.request_timeout_seconds == 1_200
    assert settings.max_concurrent_extractions == 1


def test_settings_require_a_32_character_api_token() -> None:
    with pytest.raises(ValidationError):
        Settings(api_token="x" * 31)
