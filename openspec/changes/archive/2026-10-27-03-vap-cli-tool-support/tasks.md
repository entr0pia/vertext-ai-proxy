# Tasks

## 1. CLI Script Registration & Template Definition

- [x] 1.1 Add `vap` and `vertext-ai-proxy` to `[project.scripts]` in `pyproject.toml`.
- [x] 1.2 Implement `src/vertext_ai_proxy/core/templates.py` containing `DEFAULT_CONFIG_TEMPLATE`.

## 2. CLI Implementation

- [x] 2.1 Implement `src/vertext_ai_proxy/cli.py` with `vap init` subcommand and `--force` overwrite protection.
- [x] 2.2 Implement `vap run` subcommand in `src/vertext_ai_proxy/cli.py` supporting configuration resolution and CLI overrides (`--config`, `--host`, `--port`, `--log-level`).

## 3. Verification & Testing

- [x] 3.1 Write unit tests in `tests/test_cli.py` testing `vap init`, `--force`, `vap run` argument parsing, and `--version`.
- [x] 3.2 Run full automated test suite (`$env:PYTHONUTF8=1; uv run pytest -v`) and verify all tests pass.
- [x] 3.3 Test `uv tool install . --force` and verify standalone CLI execution in a clean temporary directory.
- [x] 3.4 Update `README.md` and `AGENTS.md` documenting `uv tool install`, `vap init`, and `vap run`.

## Workflow follow-up

- Archive the change after the project's review requirements are satisfied.
- Verify the archived result.
