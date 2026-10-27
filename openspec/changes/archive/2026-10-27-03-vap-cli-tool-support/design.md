# Design

## Context

See `proposal.md` for background and user goals. Users installing via `uv tool install` need a clean command `vap` providing `init` and `run` subcommands.

## Goals / Non-Goals

**Goals:**
- Provide executable `vap` (and alias `vertext-ai-proxy`) via `[project.scripts]`.
- Implement `vap init` with overwrite protection and `--force` support.
- Implement `vap run` supporting optional CLI overrides (`--config`, `--host`, `--port`, `--log-level`).
- Zero extra dependencies: implemented purely via Python standard library `argparse`.
- Embed default configuration template so `vap init` works anywhere on the filesystem.

**Non-Goals:**
- Interactive GUI or TUI configuration wizard.
- Remote deployment orchestrations.

## Decisions

### 1. CLI Module Structure
Implement `src/vertext_ai_proxy/cli.py` with:
- `build_parser()`: creates `argparse.ArgumentParser(prog="vap")` with subparsers for `init` and `run`.
- `handle_init(args)`: handles config file writing and overwrite check.
- `handle_run(args)`: resolves configuration, applies CLI flag overrides, validates config, and invokes Uvicorn.
- `main()`: parses arguments and dispatches to handler.

### 2. Template Storage & Loading
Store a default YAML template in `src/vertext_ai_proxy/core/templates.py` as a well-documented constant `DEFAULT_CONFIG_TEMPLATE`, while also writing `src/vertext_ai_proxy/templates/config.template.yaml`:
- *Rationale*: Storing the template text directly in code guarantees 100% reliability regardless of wheel packaging modes, zip imports, or PyInstaller bundling, while keeping `config.example.yaml` at the repo root synchronized.

### 3. Argument Overrides in `vap run`
`vap run` loads the configuration via `load_config(args.config)`. If `--host`, `--port`, or `--log-level` are passed, they explicitly override the values in `config.server`.

## Risks / Trade-offs

- **[Risk] User runs `vap run` before editing `config.local.yaml`**  
  *Mitigation*: `validate_config` halts execution with a user-friendly message explaining that placeholder keys must be replaced.
