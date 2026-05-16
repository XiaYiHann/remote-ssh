# remote-ssh-cli

CLI tool for managing GPU debug jobs and SSH connections on **SZU AI Cloud**.

Inspired by the Claude Code `remote-ssh` skill, this package extracts the core automation into an independent, pip-installable command-line tool so you can use it **without Claude Code**.

## Features

- **Dry-run mode**: resolve all backend IDs and preview the job config before actually creating anything.
- **Create debug jobs**: single-card 4090, multi-card H100/H200 — all controllable from the CLI.
- **SSH auto-resolution**: polls the debug URL endpoint until SSH is ready, then prints OS-specific SSH commands.
- **Resource listing**: browse available GPU pools and power configs across clusters.
- **Credential fallback**: reads `SZU_AICLOUD_USERNAME` / `PASSWORD` from environment variables or `~/.bashrc` exports.

## Installation

```bash
pip install remote-ssh-cli
```

Or install from source:

```bash
git clone https://github.com/yourusername/remote-ssh-cli.git
cd remote-ssh-cli
pip install -e ".[dev]"
```

## Quick Start

### 1. Set credentials (optional)

```bash
export SZU_AICLOUD_USERNAME="your_username"
export SZU_AICLOUD_PASSWORD="your_password"
export SZU_AICLOUD_KEY_PATH="~/.ssh/id_rsa"
export SZU_AICLOUD_PROXY="http://127.0.0.1:7890"   # optional
```

Or add them to `~/.bashrc` — the tool will read them automatically.

### 2. List available GPUs

```bash
remote-ssh list-resources
```

### 3. Dry-run a job

```bash
remote-ssh create --gpu 4090 --duration-hours 1 --card-num 1
```

### 4. Create and wait for SSH

```bash
remote-ssh create --submit --wait-ssh --gpu H100 --card-num 4 --duration-hours 4
```

### 5. Get SSH info for an existing job

```bash
remote-ssh ssh <job_id> --wait-ssh
```

## Commands

| Command | Description |
|---|---|
| `remote-ssh create` | Dry-run or create a debug job |
| `remote-ssh ssh <job_id>` | Resolve SSH commands for an existing job |
| `remote-ssh list-resources` | List available GPU training options |

## Common Options

| Option | Default | Description |
|---|---|---|
| `--submit` | `false` | Actually create the job (without this, dry-run only) |
| `--wait-ssh` | `false` | Poll until SSH state is ready |
| `--gpu` | `4090` | GPU keyword to match (e.g. `H100`, `H200`) |
| `--card-num` | `1` | Number of GPU cards |
| `--duration-hours` | `1` | Debug job duration |
| `--team` | `eth.ai` | Team name |
| `--key-path` | `~/.ssh/id_rsa` | SSH private key path |
| `--proxy` | — | HTTP proxy for Playwright browser |
| `--headed` | `false` | Show browser window (useful for debugging login) |

## Project Structure

```
remote-ssh-cli/
├── src/remote_ssh_cli/
│   ├── cli.py       # Typer command-line interface
│   ├── client.py    # Playwright browser automation
│   ├── config.py    # Data classes and constants
│   ├── selectors.py # Backend record selection logic
│   ├── ssh.py       # SSH command generation
│   └── utils.py     # Credential and response helpers
├── tests/
├── pyproject.toml
└── README.md
```

## Development

```bash
# Install in editable mode with dev dependencies
pip install -e ".[dev]"

# Lint
ruff check src tests
ruff format --check src tests

# Type check
mypy src

# Run tests
pytest
```

## License

MIT
