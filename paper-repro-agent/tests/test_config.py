from paper_parser.config import Settings


def test_default_settings_are_safe():
    settings = Settings(parser_api_token="x" * 32)
    assert settings.max_upload_mb == 50
    assert settings.max_pages == 400
    assert settings.max_concurrent_jobs == 1
    assert settings.work_dir == "/data/jobs"


def test_short_token_is_rejected():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(parser_api_token="short")
