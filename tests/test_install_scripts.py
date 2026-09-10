"""Contract checks for the public RinCode installers."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["install.sh", "install.ps1"])
def test_installer_requires_explicit_wheel_without_default_upstream_release(name: str) -> None:
    source = (ROOT / name).read_text(encoding="utf-8")

    assert "releases/latest" not in source
    assert "htxoffical" not in source
    assert "rincode_harness" in source
    assert "RINCODE_WHEEL_URL" in source
    assert "RINCODE_GITEE_TOKEN" in source
    assert "RINCODE_NPM_REGISTRY" in source
    assert "RINCODE_NODE_CHECKSUM_BASE" in source
    assert "RINCODE_PYPI_INDEX" in source
    assert "github.com" not in source.lower()
    assert "myna" not in source.lower()
    assert "--with-executables-from" not in source
    assert "rincode onboard --skip-memory" in source


def test_posix_installer_downloads_private_wheel_before_uv_install() -> None:
    source = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert "gitee_curl" in source
    assert '-H "Authorization: Bearer $RINCODE_GITEE_TOKEN"' not in source
    assert '"$wheel_url" -o "$wheel_path"' in source
    assert 'uv tool install --force "rincode-harness[channels] @ $wheel_source"' in source


def test_powershell_installer_downloads_private_wheel_before_uv_install() -> None:
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")

    assert '"Authorization" = "Bearer $env:RINCODE_GITEE_TOKEN"' in source
    assert "Invoke-WebRequest $wheelUrl -Headers $headers -OutFile $wheelPath" in source
    assert '"rincode-harness[channels] @ $wheelSource"' in source


@pytest.mark.parametrize("name", ["install.sh", "install.ps1"])
def test_installer_fails_closed_when_node_checksum_is_unavailable(name: str) -> None:
    source = (ROOT / name).read_text(encoding="utf-8")

    assert "skipping checksum verification" not in source.lower()
    assert "could not fetch node shasums256.txt" in source.lower()
    assert "https://nodejs.org/dist" in source


@pytest.fixture
def installer_sandbox(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    """Run the real shell script with local tool doubles; network is forbidden."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    shutil.copy2(ROOT / "install.sh", checkout / "install.sh")
    tool_bin = tmp_path / "bin"
    tool_bin.mkdir()
    log = tmp_path / "commands.log"
    commands = {
        "node": "printf 'v22.20.0\\n'\n",
        "curl": 'echo "unexpected network request" >&2; exit 99\n',
        "uv": (
            'printf "uv %s\\n" "$*" >> "$INSTALL_LOG"\n'
            'if [ "${FAIL_CHANNELS:-}" = 1 ]; then\n'
            '  case "$*" in *"[channels]"*) exit 1 ;; esac\n'
            "fi\n"
        ),
        "npm": 'printf "npm %s\\n" "$*" >> "$INSTALL_LOG"\n',
    }
    for name, body in commands.items():
        executable = tool_bin / name
        executable.write_text("#!/bin/sh\n" + body)
        executable.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("RINCODE_")}
    env.update(
        PATH=f"{tool_bin}{os.pathsep}{os.environ['PATH']}",
        HOME=str(tmp_path),
        RINCODE_HOME=str(tmp_path / ".rincode"),
        INSTALL_LOG=str(log),
    )
    return checkout, env, log


@pytest.mark.parametrize("fallback", [False, True])
def test_shell_installs_local_source_and_builds_tui(installer_sandbox, fallback: bool) -> None:
    checkout, env, log = installer_sandbox
    (checkout / "pyproject.toml").write_text('name = "rincode-harness"\n')
    (checkout / "ui-tui").mkdir()
    if fallback:
        env["FAIL_CHANNELS"] = "1"
    result = subprocess.run(["sh", str(checkout / "install.sh")], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    calls = log.read_text()
    assert "npm ci --registry https://registry.npmmirror.com" in calls
    assert "npm run build" in calls
    assert f"uv tool install --force -e {checkout}[channels]" in calls
    if fallback:
        assert f"uv tool install --force -e {checkout}\n" in calls
    assert "rincode onboard --skip-memory" in result.stdout


@pytest.mark.parametrize("wheel", [None, "/tmp/upstream_harness-0.1.0-py3-none-any.whl"])
def test_shell_rejects_missing_or_upstream_wheel(installer_sandbox, wheel: str | None) -> None:
    checkout, env, log = installer_sandbox
    if wheel:
        env["RINCODE_WHEEL_URL"] = wheel
    result = subprocess.run(["sh", str(checkout / "install.sh")], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "RINCODE_WHEEL_URL" in result.stderr or "Expected a rincode_harness wheel" in result.stderr
    assert "uv tool install" not in log.read_text()
    assert "unexpected network request" not in result.stderr


@pytest.mark.parametrize("fallback", [False, True])
def test_shell_installs_explicit_local_wheel(installer_sandbox, fallback: bool) -> None:
    checkout, env, log = installer_sandbox
    wheel = checkout / "rincode_harness-0.1.0-py3-none-any.whl"
    wheel.touch()
    env["RINCODE_WHEEL_URL"] = str(wheel)
    if fallback:
        env["FAIL_CHANNELS"] = "1"
    result = subprocess.run(["sh", str(checkout / "install.sh")], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    calls = log.read_text()
    assert f"uv tool install --force rincode-harness[channels] @ {wheel}" in calls
    if fallback:
        assert f"uv tool install --force rincode-harness @ {wheel}" in calls
