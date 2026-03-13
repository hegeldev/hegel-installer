# hegel-installer

A portable bash script that installs [hegel-core](https://github.com/hegeldev/hegel-core) into a shared per-user cache directory.

## Requirements

- **bash** (any recent version)
- **[uv](https://docs.astral.sh/uv/)** — fast Python package installer

## Usage

```bash
HEGEL_VERSION=v0.4.0 bash install-hegel.sh
```

The script prints the absolute path to the installed `hegel` binary on stdout. All other output (progress messages, errors) goes to stderr.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `HEGEL_VERSION` | Yes | Git tag of hegel-core to install (e.g. `v0.4.0`) |
| `XDG_CACHE_HOME` | No | Override default cache location on Linux |

## Cache Locations

| Platform | Directory |
|----------|-----------|
| macOS | `$HOME/Library/Caches/hegel/versions/$HEGEL_VERSION/` |
| Linux | `${XDG_CACHE_HOME:-$HOME/.cache}/hegel/versions/$HEGEL_VERSION/` |

Each version gets its own isolated virtualenv under the cache directory.

## Concurrency

The script is safe to run concurrently. It installs into a temporary directory and atomically moves it into place. If two processes race, the loser detects the winner's installation and uses it.

## How SDKs Consume This

SDKs vendor `install-hegel.sh` into their repository and run it at build/test time:

```rust
// Rust example: embed the script at compile time
const INSTALL_SCRIPT: &str = include_str!("../scripts/install-hegel.sh");
```

```bash
# Shell example: run directly
hegel_path=$(HEGEL_VERSION=v0.4.0 bash scripts/install-hegel.sh)
```
