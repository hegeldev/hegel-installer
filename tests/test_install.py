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


class TestInputValidation:
    def test_missing_hegel_version(self):
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

    def test_missing_uv(self, isolated_home, tmp_path):
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


class TestInstallation:
    def test_successful_install(self, isolated_home):
        """Real install with current hegel-core tag succeeds."""
        env = cache_env(isolated_home)
        result = run_installer(env_overrides=env)
        binary_path = result.stdout.strip()
        assert binary_path.endswith("/bin/hegel")
        assert os.path.isfile(binary_path)
        assert os.access(binary_path, os.X_OK)

    def test_idempotent_rerun(self, isolated_home):
        """Second run returns same path and is fast (cache hit)."""
        env = cache_env(isolated_home)
        result1 = run_installer(env_overrides=env)
        result2 = run_installer(env_overrides=env)
        assert result1.stdout.strip() == result2.stdout.strip()
        # Second run should not print "Installing..." since it hits cache
        assert "Installing" not in result2.stderr

    def test_correct_platform_cache_dir(self, isolated_home):
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


class TestConcurrency:
    def test_concurrent_install(self, isolated_home):
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


class TestRecovery:
    def test_stale_temp_dir_cleanup(self, isolated_home):
        """Installer cleans up stale .install-* temp dirs."""
        env = cache_env(isolated_home)

        # Determine cache base
        if platform.system() == "Darwin":
            versions_dir = (
                isolated_home / "Library" / "Caches" / "hegel" / "versions"
            )
        else:
            cache_dir = Path(
                env.get("XDG_CACHE_HOME", str(isolated_home / ".cache"))
            )
            versions_dir = cache_dir / "hegel" / "versions"

        versions_dir.mkdir(parents=True)
        stale_dir = versions_dir / ".install-STALE123"
        stale_dir.mkdir()
        (stale_dir / "marker").touch()

        result = run_installer(env_overrides=env)
        assert result.returncode == 0
        assert not stale_dir.exists()


class TestStdoutCleanliness:
    def test_stdout_is_single_line(self, isolated_home):
        """Stdout contains exactly one line: the binary path."""
        env = cache_env(isolated_home)
        result = run_installer(env_overrides=env)
        lines = result.stdout.strip().splitlines()
        assert len(lines) == 1


class TestErrorConditions:
    def test_uv_install_failure(self, isolated_home, tmp_path):
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

        # Verify no leftover incomplete version dirs
        if platform.system() == "Darwin":
            versions_dir = (
                isolated_home / "Library" / "Caches" / "hegel" / "versions"
            )
        else:
            cache_dir = Path(
                env.get("XDG_CACHE_HOME", str(isolated_home / ".cache"))
            )
            versions_dir = cache_dir / "hegel" / "versions"

        # The version dir should have been cleaned up on failure
        version_dir = versions_dir / HEGEL_VERSION
        assert not version_dir.exists(), f"Leftover version dir: {version_dir}"

    def test_partial_install_recovery(self, isolated_home):
        """Version dir with missing binary triggers reinstall."""
        env = cache_env(isolated_home)

        # Create a version dir without the binary
        if platform.system() == "Darwin":
            version_dir = (
                isolated_home
                / "Library"
                / "Caches"
                / "hegel"
                / "versions"
                / HEGEL_VERSION
            )
        else:
            cache_dir = Path(
                env.get("XDG_CACHE_HOME", str(isolated_home / ".cache"))
            )
            version_dir = cache_dir / "hegel" / "versions" / HEGEL_VERSION

        version_dir.mkdir(parents=True)
        # Dir exists but no binary — installer should handle this

        result = run_installer(env_overrides=env)
        binary_path = result.stdout.strip()
        assert os.path.isfile(binary_path)
