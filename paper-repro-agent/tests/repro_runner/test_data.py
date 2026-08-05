from repro_runner.config import Settings
from repro_runner.schemas import ExperimentConfig


def test_defaults_match_v2_contract():
    settings = Settings()
    config = ExperimentConfig()

    assert settings.max_upload_mb == 100
    assert settings.max_columns == 256
    assert config.model == "random_forest"
    assert config.test_size == 0.2
    assert config.random_state == 42
