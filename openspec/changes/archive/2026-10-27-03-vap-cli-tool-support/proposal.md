# Proposal

## Why

Developers utilizing modern Python toolchains like `uv` often install tools globally via `uv tool install` or run them on demand with `uvx`. Currently, `vertext-ai-proxy` requires manual directory navigation, manual config file copying (`Copy-Item config.example.yaml config.local.yaml`), and running via `python -m vertext_ai_proxy.app`. 

Providing a unified, lightweight CLI executable `vap` with subcommands `vap init` (to scaffold a local configuration file in any directory) and `vap run` (to start the proxy server) drastically streamlines installation, onboarding, and day-to-day operation.

## What Changes

- **CLI Script Registration**: Register `vap` (and alias `vertext-ai-proxy`) in `pyproject.toml` under `[project.scripts]`.
- **`vap init` Subcommand**: Creates `config.local.yaml` in the current working directory from an embedded template. Rejects overwriting existing files unless `--force` is specified.
- **`vap run` Subcommand**: Starts the proxy server, automatically detecting `config.local.yaml` or `config.yaml`, with optional CLI overrides (`--config`, `--host`, `--port`, `--log-level`).
- **Embedded Configuration Template**: Bundle a pristine `config.template.yaml` inside the Python package so `vap init` works in any directory after standalone installation.

## Capabilities

### New Capabilities
*(None)*

### Modified Capabilities
- `openai-proxy`: Add requirement for CLI Interface and Scaffolding (`vap init` and `vap run`) to manage local configurations and launch the server from the command line.

## Impact

- **CLI & Packaging**: Adds `vap` executable and `vertext_ai_proxy.cli` module.
- **Dependencies**: No external dependencies added (uses Python standard library `argparse`).
- **Backwards Compatibility**: Existing `python -m vertext_ai_proxy.app` and `python -m vertext_ai_proxy.main` remain 100% functional.
