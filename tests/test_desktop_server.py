"""Tests for rincode.cli.desktop_server adapter module.

Verifies:
1. Socket creation and single ready line emission {address, token}.
2. Ephemeral loopback TCP listener and token authentication.
3. Subprocess integration tests with real system.hello and system.ping.
4. Real session.create and session.list using temp product home isolation (no model secrets).
5. Clean process termination and full runtime cleanup on stdin EOF (no zombies).
6. Clean process termination on SIGTERM.
7. Disconnect and accept timeout behavior.
8. Auth rejection on token mismatch.
9. Desktop project cwd overriding configured agents.defaults.workspace.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from unittest.mock import MagicMock

import pytest

from rincode.cli.desktop_server import (
    accept_client,
    create_server_socket,
    emit_ready_line,
    install_desktop_project_paths,
    start_stdin_eof_watcher,
)


def get_python_exe() -> str:
    """Resolve python executable from RINCODE_PYTHON or sys.executable."""
    if "RINCODE_PYTHON" in os.environ and os.path.exists(os.environ["RINCODE_PYTHON"]):
        return os.environ["RINCODE_PYTHON"]
    return sys.executable


def get_worktree_root() -> Path:
    """Return the root path of this worktree."""
    return Path(__file__).resolve().parent.parent


class DesktopRpcClient:
    """Simple JSON-RPC client over socket for testing desktop_server."""

    def __init__(self, host: str, port: int, token: str):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(10.0)
        self.sock.connect((host, port))
        self.sock.sendall((token + "\n").encode("utf-8"))
        self.buf = b""
        self.req_id = 0

    def call(self, method: str, params: dict | None = None, timeout: float = 10.0) -> dict:
        self.req_id += 1
        rid = self.req_id
        req = {
            "jsonrpc": "2.0",
            "id": rid,
            "method": method,
            "params": params if params is not None else {},
        }
        self.sock.sendall((json.dumps(req) + "\n").encode("utf-8"))
        self.sock.settimeout(timeout)
        while b"\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("Connection closed by server")
            self.buf += chunk
        line, _, self.buf = self.buf.partition(b"\n")
        return json.loads(line.decode("utf-8"))

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def _spawn_server(
    tmp_path: Path,
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.Popen[bytes], str, int, str]:
    """Helper to spawn desktop_server subprocess with isolated RINCODE_HOME."""
    product_home = tmp_path / "rincode-home"
    product_home.mkdir(parents=True, exist_ok=True)
    worktree = get_worktree_root()

    env = os.environ.copy()
    env["PYTHONPATH"] = str(worktree)
    env["RINCODE_HOME"] = str(product_home)
    # Ensure no secrets required
    env.pop("OPENAI_API_KEY", None)
    env.pop("ANTHROPIC_API_KEY", None)
    if extra_env:
        env.update(extra_env)

    proc = subprocess.Popen(
        [get_python_exe(), "-m", "rincode.cli.desktop_server"],
        cwd=str(tmp_path),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    assert proc.stdout is not None
    # Read the single ready JSON line from stdout
    line = proc.stdout.readline()
    if not line:
        err = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
        raise AssertionError(f"Server exited without emitting ready line (code={proc.poll()}, stderr={err})")
    data = json.loads(line.decode("utf-8").strip())
    assert "address" in data and "token" in data, f"Malformed ready line: {data}"
    host, port_str = data["address"].split(":")
    return proc, host, int(port_str), data["token"]


# ===========================================================================
# Unit Tests for Adapter Server Primitives & Paths Adapter
# ===========================================================================


def test_create_server_socket_binds_ephemeral() -> None:
    """Verifies server socket binds to 127.0.0.1 with an ephemeral port."""
    sock, host, port = create_server_socket()
    try:
        assert host == "127.0.0.1"
        assert port > 0
    finally:
        sock.close()


def test_emit_ready_line(capsys: pytest.CaptureFixture[str]) -> None:
    """Verifies ready JSON line emitted to stdout matches contract format."""
    emit_ready_line("127.0.0.1", 54321, "abc123secret")
    captured = capsys.readouterr()
    line = captured.out.strip()
    data = json.loads(line)
    assert data == {"address": "127.0.0.1:54321", "token": "abc123secret"}


@pytest.mark.asyncio
async def test_accept_client_timeout() -> None:
    """Verifies accept_client returns None on timeout when no client connects."""
    sock, _, _ = create_server_socket()
    proc_done = asyncio.Event()
    try:
        conn = await accept_client(sock, accept_timeout_s=0.1, proc_done=proc_done)
        assert conn is None
    finally:
        sock.close()


@pytest.mark.asyncio
async def test_accept_client_cancelled_by_proc_done() -> None:
    """Verifies accept_client unblocks immediately when proc_done is triggered."""
    sock, _, _ = create_server_socket()
    proc_done = asyncio.Event()
    proc_done.set()  # Triggered before accept
    try:
        conn = await accept_client(sock, accept_timeout_s=5.0, proc_done=proc_done)
        assert conn is None
    finally:
        sock.close()


@pytest.mark.asyncio
async def test_stdin_eof_watcher_triggers_proc_done() -> None:
    """Verifies start_stdin_eof_watcher signals proc_done upon EOF."""
    r_fd, w_fd = os.pipe()
    orig_stdin = os.dup(0)
    try:
        os.dup2(r_fd, 0)
        os.close(r_fd)

        loop = asyncio.get_running_loop()
        proc_done = asyncio.Event()
        watcher = start_stdin_eof_watcher(proc_done, loop)
        assert watcher is not None

        # Closing the write end simulates parent pipe close / exit
        os.close(w_fd)

        # Wait for proc_done to be set
        await asyncio.wait_for(proc_done.wait(), timeout=2.0)
        assert proc_done.is_set()
    finally:
        os.dup2(orig_stdin, 0)
        os.close(orig_stdin)


def test_desktop_project_cwd_overrides_configured_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test: Desktop project cwd overrides configured agents.defaults.workspace.

    Asserts that resolve_foreground_paths selects the cwd project directory and its
    corresponding project state dir, even when Config specifies a different workspace path.
    Verifies execution directory selection without claiming a filesystem sandbox.
    """
    import rincode.config.paths as paths_module
    from rincode.product import get_project_state_dir

    project_dir = tmp_path / "desktop_project"
    project_dir.mkdir(parents=True, exist_ok=True)
    other_dir = tmp_path / "other_configured_workspace"
    other_dir.mkdir(parents=True, exist_ok=True)

    # Mock Config with agents.defaults.workspace pointing to a different directory
    mock_config = MagicMock()
    mock_config.workspace_path = str(other_dir)
    mock_config.agents.defaults.workspace = str(other_dir)

    # Save original resolver on paths_module and ensure monkeypatch restores it
    orig_resolver = paths_module.resolve_foreground_paths
    monkeypatch.setattr(paths_module, "resolve_foreground_paths", orig_resolver)

    # Switch cwd to project_dir
    monkeypatch.chdir(project_dir)

    installed_paths = install_desktop_project_paths()

    # The returned paths must match project_dir (canonical cwd)
    expected_project = project_dir.resolve()
    expected_state = get_project_state_dir(expected_project)

    assert installed_paths.workspace == expected_project
    assert installed_paths.state == expected_state

    # Now call paths_module.resolve_foreground_paths directly with mock_config
    resolved = paths_module.resolve_foreground_paths(mock_config)
    assert resolved.workspace == expected_project
    assert resolved.workspace != other_dir.resolve()
    assert resolved.state == expected_state


# ===========================================================================
# Subprocess Integration Tests (Real Runtime & RPC)
# ===========================================================================


def test_subprocess_hello_and_ping(tmp_path: Path) -> None:
    """Integration test: Launch real desktop_server subprocess, perform handshake, and ping."""
    proc, host, port, token = _spawn_server(tmp_path)
    client = DesktopRpcClient(host, port, token)
    try:
        # Handshake: system.hello
        hello_resp = client.call("system.hello", {"client_version": "0.1.0"})
        assert "result" in hello_resp, f"system.hello error: {hello_resp.get('error')}"
        assert "server_version" in hello_resp["result"]

        # Probe: system.ping
        ping_resp = client.call("system.ping", {})
        assert "result" in ping_resp, f"system.ping error: {ping_resp.get('error')}"
        assert ping_resp["result"].get("pong") is True
        assert "server_time_ms" in ping_resp["result"]
    finally:
        client.close()
        if proc.stdin:
            proc.stdin.close()
        proc.wait(timeout=5)
        assert proc.returncode == 0


def test_subprocess_session_create_and_list(tmp_path: Path) -> None:
    """Integration test: Create sessions and list them using isolated temp product home."""
    proc, host, port, token = _spawn_server(tmp_path)
    client = DesktopRpcClient(host, port, token)
    try:
        # Handshake first
        hello_resp = client.call("system.hello", {"client_version": "0.1.0"})
        assert "result" in hello_resp

        # Create session 1
        create_resp1 = client.call("session.create", {})
        assert "result" in create_resp1, f"session.create error: {create_resp1.get('error')}"
        s1 = create_resp1["result"]
        assert "session_id" in s1
        assert s1["session_id"].startswith("tui:")
        assert "info" in s1

        # Create session 2
        create_resp2 = client.call("session.create", {})
        assert "result" in create_resp2
        s2 = create_resp2["result"]
        assert s2["session_id"].startswith("tui:")
        assert s1["session_id"] != s2["session_id"]

        # List sessions
        list_resp = client.call("session.list", {})
        assert "result" in list_resp, f"session.list error: {list_resp.get('error')}"
        sessions = list_resp["result"].get("sessions")
        assert isinstance(sessions, list)
    finally:
        client.close()
        if proc.stdin:
            proc.stdin.close()
        proc.wait(timeout=5)
        assert proc.returncode == 0


def test_subprocess_termination_by_stdin_eof(tmp_path: Path) -> None:
    """Integration test: Parent closing stdin pipe causes immediate clean termination."""
    proc, host, port, token = _spawn_server(tmp_path)
    client = DesktopRpcClient(host, port, token)
    try:
        hello_resp = client.call("system.hello", {"client_version": "0.1.0"})
        assert "result" in hello_resp
    finally:
        client.close()

    # Close stdin pipe from parent
    assert proc.stdin is not None
    proc.stdin.close()

    # Subprocess must cleanly exit without becoming a zombie
    exit_code = proc.wait(timeout=5)
    assert exit_code == 0


def test_subprocess_termination_by_sigterm(tmp_path: Path) -> None:
    """Integration test: SIGTERM causes clean shutdown and cleanup."""
    proc, host, port, token = _spawn_server(tmp_path)
    client = DesktopRpcClient(host, port, token)
    try:
        hello_resp = client.call("system.hello", {"client_version": "0.1.0"})
        assert "result" in hello_resp
    finally:
        client.close()

    try:
        # Send SIGTERM
        proc.send_signal(signal.SIGTERM)
        exit_code = proc.wait(timeout=5)
        # SIGTERM handler sets proc_done which cleanly exits with 0 or terminated code
        assert exit_code in (0, 143, -signal.SIGTERM)
    finally:
        if proc.stdin and not proc.stdin.closed:
            try:
                proc.stdin.close()
            except OSError:
                pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_subprocess_termination_by_client_disconnect(tmp_path: Path) -> None:
    """Integration test: Client disconnecting TCP socket causes proc_done and exit."""
    proc, host, port, token = _spawn_server(tmp_path)
    client = DesktopRpcClient(host, port, token)
    hello_resp = client.call("system.hello", {"client_version": "0.1.0"})
    assert "result" in hello_resp

    # Client closes socket
    client.close()

    # In desktop_server, socket closure wakes proc_done and terminates
    # Wait up to 5 seconds
    try:
        exit_code = proc.wait(timeout=5)
        assert exit_code == 0
    finally:
        if proc.stdin and not proc.stdin.closed:
            try:
                proc.stdin.close()
            except OSError:
                pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_subprocess_accept_timeout(tmp_path: Path) -> None:
    """Integration test: Server exits with non-zero when no client connects within accept timeout."""
    worktree = get_worktree_root()
    product_home = tmp_path / "rincode-home"
    product_home.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(worktree)
    env["RINCODE_HOME"] = str(product_home)
    env["RINCODE_ACCEPT_TIMEOUT"] = "0.5"

    start_time = time.time()
    proc = subprocess.Popen(
        [get_python_exe(), "-m", "rincode.cli.desktop_server"],
        cwd=str(tmp_path),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    try:
        # Read ready line
        assert proc.stdout is not None
        line = proc.stdout.readline()
        assert line

        # Do not connect; wait for accept timeout
        exit_code = proc.wait(timeout=5.0)
        elapsed = time.time() - start_time
        assert exit_code != 0
        assert elapsed < 4.0
    finally:
        if proc.stdin and not proc.stdin.closed:
            try:
                proc.stdin.close()
            except OSError:
                pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_subprocess_auth_mismatch_rejected(tmp_path: Path) -> None:
    """Integration test: Client sending incorrect token is dropped and server exits."""
    proc, host, port, _ = _spawn_server(tmp_path)
    # Connect with invalid token
    client = DesktopRpcClient(host, port, "wrong-token-1234567890abcdef")
    try:
        # Attempting system.hello will fail because server closes connection
        with pytest.raises((ConnectionError, socket.error)):
            client.call("system.hello", {"client_version": "0.1.0"}, timeout=2.0)
    finally:
        client.close()
        if proc.stdin and not proc.stdin.closed:
            try:
                proc.stdin.close()
            except OSError:
                pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_subprocess_stdin_eof_before_connect(tmp_path: Path) -> None:
    """Integration test: Immediate stdin EOF before any TCP connection terminates cleanly."""
    worktree = get_worktree_root()
    product_home = tmp_path / "rincode-home"
    product_home.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(worktree)
    env["RINCODE_HOME"] = str(product_home)

    proc = subprocess.Popen(
        [get_python_exe(), "-m", "rincode.cli.desktop_server"],
        cwd=str(tmp_path),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    try:
        assert proc.stdout is not None
        line = proc.stdout.readline()
        assert line

        # Immediately close stdin without connecting
        assert proc.stdin is not None
        proc.stdin.close()

        exit_code = proc.wait(timeout=5.0)
        assert exit_code == 0
    finally:
        if proc.stdin and not proc.stdin.closed:
            try:
                proc.stdin.close()
            except OSError:
                pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
