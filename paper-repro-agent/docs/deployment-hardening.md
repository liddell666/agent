# Deployment and capacity behavior

Run `scripts/init_local_env.ps1` for a new installation. It creates independent
parser and extractor API tokens and a runner protocol signing secret, plus all
three services' baseline settings. Existing `.env` files are never overwritten.
For existing installations, add missing settings using `.env.example` while
preserving existing credentials. Configure the matching values in Dify:
`PARSER_API_TOKEN`, `DIFY_EXTRACTOR_API_TOKEN`, and `DIFY_PROTOCOL_SECRET` (the latter
must match `REPRO_RUNNER_PROTOCOL_SECRET`). Never commit real credentials.

`scripts/configure_dify_env.ps1 -DifyEnvPath <path>` backs up the target file and
merges `paper-parser`, `repro-runner`, and `paper-dossier-extractor` into existing
comma-separated SSRF domains. Repeated runs retain other domains and settings.
Recreate affected Dify containers after deliberately applying configuration.

The parser accepts one active parse per process and queues concurrent parsing
requests. Its worker holds the reservation until parsing completes or raises,
even if the caller disconnects. Keep the deployed single-worker configuration;
the lock does not coordinate multiple processes.

Each extraction transport timeout is the smaller of the configured per-call
timeout and the remaining extraction budget. Expired budgets prevent further
calls. This bounds socket waits; it is not a hard process-level deadline for
every stage, and it does not guarantee cancellation of inference on Ollama.

Focused verification:

```powershell
python -m pytest -q tests/test_api.py tests/test_deployment_scripts.py tests/paper_dossier_extractor
docker compose config --quiet
```
