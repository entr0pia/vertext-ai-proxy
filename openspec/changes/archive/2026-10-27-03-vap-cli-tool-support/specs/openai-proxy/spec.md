# Spec Delta

## ADDED Requirements

### Requirement: Command Line Interface and Scaffolding
The proxy SHALL provide a command-line interface executable named `vap` supporting subcommands to scaffold configurations (`vap init`) and launch the proxy server (`vap run`).

#### Scenario: Initializing new configuration file with vap init
- **WHEN** user executes `vap init` in a directory where `config.local.yaml` does not exist
- **THEN** the CLI generates a new `config.local.yaml` populated from the package's default configuration template and prints setup instructions

#### Scenario: Overwrite protection on vap init
- **WHEN** user executes `vap init` in a directory where `config.local.yaml` already exists without the `--force` flag
- **THEN** the CLI terminates without modifying the existing file and outputs a warning advising to use `--force` to overwrite

#### Scenario: Force overwrite on vap init
- **WHEN** user executes `vap init --force` in a directory where `config.local.yaml` already exists
- **THEN** the CLI overwrites `config.local.yaml` with the default template and confirms successful initialization

#### Scenario: Starting server with vap run
- **WHEN** user executes `vap run` in a directory containing a valid `config.local.yaml` or `config.yaml`
- **THEN** the CLI loads the configuration, validates API keys, and starts the FastAPI server using Uvicorn

#### Scenario: Custom host and port overrides on vap run
- **WHEN** user executes `vap run --host 0.0.0.0 --port 9000`
- **THEN** the CLI overrides the configuration settings and binds the server to `0.0.0.0:9000`
