"""Tests for install-hegel.sh."""

import os
import platform
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parent.parent / "install-hegel.sh"
# Use a known recent tag for real installation tests
HEGEL_VERSION = "v0.4.0"


def run_installer(
    env_overrides: dict | None = None,
    version: str = HEGEL_VERSION,
    expect_fail: bool = False,
) -> subprocess.CompletedProcess:
    """Run install-hegel.sh with the given environment."""
    env = os.environ.copy()
    if version:
        env["HEGEL_VERSION"] = version
    if env_overrides:
        env.update(env_overrides)
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if not expect_fail:
        assert result.returncode == 0, (
            f"Installer failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
    return result


@pytest.fixture
def isolated_home(tmp_path):
    """Provide an isolated HOME directory for cache isolation."""
    home = tmp_path / "home"
    home.mkdir()
    return home


def cache_env(isolated_home: Path) -> dict:
    """Return env overrides that redirect the cache to an isolated directory."""
    if platform.system() == "Darwin":
        cache_dir = isolated_home / "Library" / "Caches"
        cache_dir.mkdir(parents=True)
        return {"HOME": str(isolated_home)}
    else:
        cache_dir = isolated_home / "cache"
        cache_dir.mkdir(parents=True)
        return {"XDG_CACHE_HOME": str(cache_dir)}


def versions_dir_for(isolated_home: Path, env: dict) -> Path:
    """Return the versions directory for the given isolated home."""
    if platform.system() == "Darwin":
        return isolated_home / "Library" / "Caches" / "hegel" / "versions"
    else:
        cache_dir = Path(env.get("XDG_CACHE_HOME", str(isolated_home / ".cache")))
        return cache_dir / "hegel" / "versions"


def test_missing_hegel_version():
    """Installer errors when HEGEL_VERSION is not set."""
    env = os.environ.copy()
    env.pop("HEGEL_VERSION", None)
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "HEGEL_VERSION" in result.stderr


def test_missing_uv(isolated_home):
    """Installer errors when uv is not on PATH."""
    env = cache_env(isolated_home)
    # Set PATH to only contain directories with bash but not uv
    # We need bash itself to run, so include /bin and /usr/bin
    # but create an isolated PATH that excludes uv
    path_dirs = []
    for d in ["/bin", "/usr/bin"]:
        if os.path.isdir(d):
            path_dirs.append(d)
    env["PATH"] = ":".join(path_dirs)
    env["HEGEL_VERSION"] = HEGEL_VERSION
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "uv" in result.stderr


def test_successful_install(isolated_home):
    """Real install with current hegel-core tag succeeds."""
    env = cache_env(isolated_home)
    result = run_installer(env_overrides=env)
    binary_path = result.stdout.strip()
    assert binary_path.endswith("/bin/hegel")
    assert os.path.isfile(binary_path)
    assert os.access(binary_path, os.X_OK)


def test_idempotent_rerun(isolated_home):
    """Second run returns same path and is fast (cache hit)."""
    env = cache_env(isolated_home)
    result1 = run_installer(env_overrides=env)
    result2 = run_installer(env_overrides=env)
    assert result1.stdout.strip() == result2.stdout.strip()
    # Second run should not print "Installing..." since it hits cache
    assert "Installing" not in result2.stderr


def test_correct_platform_cache_dir(isolated_home):
    """Verify installed path matches platform convention."""
    env = cache_env(isolated_home)
    result = run_installer(env_overrides=env)
    binary_path = result.stdout.strip()

    if platform.system() == "Darwin":
        expected_prefix = str(
            isolated_home / "Library" / "Caches" / "hegel" / "versions"
        )
    else:
        cache_dir = env.get("XDG_CACHE_HOME", str(isolated_home / ".cache"))
        expected_prefix = str(Path(cache_dir) / "hegel" / "versions")

    assert binary_path.startswith(expected_prefix)


def test_concurrent_install(isolated_home):
    """Multiple concurrent installs all succeed and return same path."""
    env = cache_env(isolated_home)
    n_workers = 5

    def do_install(_):
        return run_installer(env_overrides=env)

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        results = list(pool.map(do_install, range(n_workers)))

    paths = [r.stdout.strip() for r in results]
    # All should return the same path
    assert len(set(paths)) == 1
    # All should succeed
    assert all(r.returncode == 0 for r in results)


def test_stale_temp_dir_cleanup(isolated_home):
    """Installer cleans up stale .install-* temp dirs."""
    env = cache_env(isolated_home)
    vdir = versions_dir_for(isolated_home, env)
    vdir.mkdir(parents=True)
    stale_dir = vdir / ".install-STALE123"
    stale_dir.mkdir()
    (stale_dir / "marker").touch()

    result = run_installer(env_overrides=env)
    assert result.returncode == 0
    assert not stale_dir.exists()


def test_stdout_is_single_line(isolated_home):
    """Stdout contains exactly one line: the binary path."""
    env = cache_env(isolated_home)
    result = run_installer(env_overrides=env)
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 1


def test_uv_install_failure(isolated_home, tmp_path):
    """Mock uv to fail during pip install; verify clean error and no leftover state."""
    env = cache_env(isolated_home)

    # Create a fake uv that succeeds for 'venv' but fails for 'pip install'
    fake_uv = tmp_path / "bin" / "uv"
    fake_uv.parent.mkdir(parents=True)
    fake_uv.write_text(
        '#!/usr/bin/env bash\n'
        'if [[ "$1" == "venv" ]]; then\n'
        '    # Create a minimal venv structure\n'
        '    mkdir -p "$2/bin"\n'
        '    touch "$2/bin/python"\n'
        '    exit 0\n'
        'fi\n'
        'echo "pip install failed" >&2\n'
        'exit 1\n'
    )
    fake_uv.chmod(0o755)

    env["PATH"] = f"{fake_uv.parent}:/bin:/usr/bin"
    result = run_installer(env_overrides=env, expect_fail=True)
    assert result.returncode != 0

    # The version dir should have been cleaned up on failure
    version_dir = versions_dir_for(isolated_home, env) / HEGEL_VERSION
    assert not version_dir.exists(), f"Leftover version dir: {version_dir}"


def test_partial_install_recovery(isolated_home):
    """Version dir with missing binary triggers reinstall."""
    env = cache_env(isolated_home)
    version_dir = versions_dir_for(isolated_home, env) / HEGEL_VERSION
    version_dir.mkdir(parents=True)
    # Dir exists but no binary — installer should handle this

    result = run_installer(env_overrides=env)
    binary_path = result.stdout.strip()
    assert os.path.isfile(binary_path)
