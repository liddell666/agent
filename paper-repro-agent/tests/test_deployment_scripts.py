import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHELL = shutil.which('pwsh') or shutil.which('powershell')
pytestmark = pytest.mark.skipif(not SHELL, reason='PowerShell required')


def run(script, *args):
    return subprocess.run([SHELL, '-NoProfile', '-File', str(script), *map(str, args)],
                          capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)


def test_allowlist_preserves_existing_domains_on_repeated_runs(tmp_path):
    env = tmp_path / '.env'
    env.write_text('UNRELATED=keep\nSSRF_PROXY_ALLOW_PRIVATE_DOMAINS=existing,paper-parser\n')
    for _ in range(2):
        result = run(ROOT / 'scripts/configure_dify_env.ps1', '-DifyEnvPath', env)
        assert result.returncode == 0, result.stderr
    lines = env.read_text(encoding='utf-8-sig').splitlines()
    assert 'UNRELATED=keep' in lines
    domains = next(line.split('=', 1)[1] for line in lines if line.startswith('SSRF_PROXY_ALLOW_PRIVATE_DOMAINS='))
    assert set(domains.split(',')) == {'existing', 'paper-parser', 'repro-runner', 'paper-dossier-extractor'}
    assert len(domains.split(',')) == 4
    assert len(list(tmp_path.glob('.env.backup-*'))) == 2


def test_init_generates_independent_secrets_and_refuses_overwrite(tmp_path):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    script = scripts / 'init_local_env.ps1'
    shutil.copyfile(ROOT / 'scripts/init_local_env.ps1', script)
    result = run(script)
    assert result.returncode == 0, result.stderr
    original = (tmp_path / '.env').read_bytes()
    values = dict(line.split('=', 1) for line in original.decode('utf-8-sig').splitlines())
    secrets = [values[key] for key in ('PAPER_PARSER_API_TOKEN', 'PAPER_DOSSIER_EXTRACTOR_API_TOKEN', 'REPRO_RUNNER_PROTOCOL_SECRET')]
    assert len(set(secrets)) == 3
    assert all(len(secret) >= 32 and secret not in result.stdout for secret in secrets)
    assert values['REPRO_RUNNER_JOB_STORE_PATH'] == '/data/experiments/jobs.sqlite3'
    assert run(script).returncode != 0
    assert (tmp_path / '.env').read_bytes() == original
