"""Desktop adapter server for RinCode macOS Electron application.

Entry point: python -m rincode.cli.desktop_server, cwd=project.

Lifecycle and architecture:
1. Initialize log redirection to desktop.log (diagnose=False, no terminal sink)
   to ensure diagnostics/tokens do not leak and stdout remains clean.
2. Install process-local adapter for rincode.config.paths.resolve_foreground_paths
   so project cwd overrides agents.defaults.workspace without modifying user config.
3. Bind ephemeral TCP socket to 127.0.0.1:0.
4. Generate random 32-byte hex authentication token.
5. Emit exactly one JSON line {"address": "127.0.0.1:<port>", "token": "<token>"}
   to stdout and flush BEFORE the runtime redirects stdout/stderr.
6. Set up signal handlers (SIGTERM, SIGINT, SIGHUP) and a background thread
   monitoring stdin for EOF (using a duplicated file descriptor to be completely
   immune to stdout/stderr redirection).
7. Accept one connection on the listening socket within an accept timeout.
8. Hand off connection to ``rincode.cli.tui_commands._run_rpc_server_until_done``,
   which validates the token, runs the JSON-RPC dispatcher, binds the runtime,
   and redirects stdio to logs.
9. Any of stdin EOF, SIGTERM, SIGINT, or socket disconnection triggers ``proc_done``,
   prompting full cooperative runtime cleanup so no zombie process is left behind.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import sys
import threading
from typing import Any

from loguru import logger

from rincode.cli._exit import flush_and_hard_exit, lancedb_finalization_hazard
from rincode.cli._log_file import redirect_loguru_to_file
from rincode.cli.tui_commands import _run_rpc_server_until_done
from rincode.config.paths import RuntimePaths
from rincode.product import get_project_state_dir

DEFAULT_ACCEPT_TIMEOUT_S: float = 10.0
DEFAULT_HANDSHAKE_DEADLINE_S: float = 5.0


def install_desktop_project_paths(project_dir: Path | None = None) -> RuntimePaths:
    """Install process-local adapter for rincode.config.paths.resolve_foreground_paths.

    In Desktop mode, the project directory (cwd) must override any configured
    agents.defaults.workspace without modifying user configuration files or TUI code.

    Both the runtime builder (_build_tui_runtime) and session._get_or_build_manager
    dynamically import resolve_foreground_paths from rincode.config.paths, so
    replacing it here ensures both agree on the canonical project and state paths.

    Note: This is execution directory selection only, not a filesystem sandbox.

    Returns the canonical RuntimePaths(workspace=project, state=get_project_state_dir(project)).
    """
    import rincode.config.paths as paths_module

    project = (project_dir or Path.cwd()).resolve()
    canonical_paths = RuntimePaths(
        workspace=project,
        state=get_project_state_dir(project),
    )

    def _desktop_resolve_foreground_paths(
        config: Any,
        *,
        workspace: str | None = None,
        cwd: Path | None = None,
    ) -> RuntimePaths:
        return canonical_paths

    paths_module.resolve_foreground_paths = _desktop_resolve_foreground_paths
    return canonical_paths


def create_server_socket() -> tuple[socket.socket, str, int]:
    """Create and bind a loopback TCP listening socket on an ephemeral port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    host, port = sock.getsockname()[:2]
    return sock, host, port


def emit_ready_line(host: str, port: int, token: str) -> None:
    """Print the single JSON ready line to stdout and flush immediately.

    Must run BEFORE _run_rpc_server_until_done redirects stdout to logs.
    """
    payload = json.dumps({"address": f"{host}:{port}", "token": token})
    sys.stdout.write(payload + "\n")
    sys.stdout.flush()


def start_stdin_eof_watcher(
    proc_done: asyncio.Event,
    loop: asyncio.AbstractEventLoop,
) -> threading.Thread | None:
    """Start a background daemon thread watching fd 0 for EOF.

    Duplicates fd 0 up front so the watcher descriptor remains independent of
    any subsequent file descriptor redirects (e.g. redirect_terminal_fds_to_file).
    When the parent process closes stdin or exits, os.read returns b"" and sets
    proc_done on the event loop.
    """
    try:
        stdin_fd = os.dup(0)
    except OSError:
        # Stdin is closed or invalid; signal proc_done immediately
        loop.call_soon_threadsafe(proc_done.set)
        return None

    def _worker() -> None:
        try:
            while True:
                chunk = os.read(stdin_fd, 1024)
                if not chunk:
                    break
        except Exception:
            pass
        finally:
            try:
                os.close(stdin_fd)
            except OSError:
                pass
            try:
                if not loop.is_closed():
                    loop.call_soon_threadsafe(proc_done.set)
            except Exception:
                pass

    thread = threading.Thread(target=_worker, daemon=True, name="DesktopServerStdinWatcher")
    thread.start()
    return thread


def setup_signal_handlers(
    proc_done: asyncio.Event,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Register SIGTERM, SIGINT, and SIGHUP to trigger proc_done."""

    def _on_signal() -> None:
        try:
            if not loop.is_closed():
                loop.call_soon_threadsafe(proc_done.set)
        except Exception:
            pass

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except (NotImplementedError, RuntimeError, ValueError):
            try:
                signal.signal(sig, lambda _s, _f: _on_signal())
            except (ValueError, OSError):
                pass

    if hasattr(signal, "SIGHUP"):
        try:
            loop.add_signal_handler(signal.SIGHUP, _on_signal)
        except (NotImplementedError, RuntimeError, ValueError):
            try:
                signal.signal(signal.SIGHUP, lambda _s, _f: _on_signal())
            except (ValueError, OSError):
                pass


async def accept_client(
    server_sock: socket.socket,
    accept_timeout_s: float,
    proc_done: asyncio.Event,
) -> socket.socket | None:
    """Accept one TCP connection, cooperatively cancellable by proc_done or timeout."""
    server_sock.setblocking(False)
    loop = asyncio.get_running_loop()
    accept_task = asyncio.create_task(loop.sock_accept(server_sock))
    proc_done_task = asyncio.create_task(proc_done.wait())
    done, pending = await asyncio.wait(
        {accept_task, proc_done_task},
        timeout=accept_timeout_s,
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        t.cancel()
    for t in pending:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass

    if accept_task in done and not accept_task.cancelled():
        try:
            conn, _ = accept_task.result()
            conn.setblocking(False)
            return conn
        except Exception:
            return None
    return None


async def _watch_conn_closed(
    conn: socket.socket,
    proc_done: asyncio.Event,
) -> None:
    """Monitor accepted connection socket closure.

    When RpcServer finishes serve_forever (e.g. client disconnect), it shuts down
    its transport which closes the socket, causing fileno() to become -1.
    This wakes up proc_done so _run_rpc_server_until_done does not wait forever.
    """
    while not proc_done.is_set():
        try:
            if conn.fileno() == -1:
                proc_done.set()
                break
        except Exception:
            proc_done.set()
            break
        await asyncio.sleep(0.1)


async def run_desktop_server(
    accept_timeout_s: float = DEFAULT_ACCEPT_TIMEOUT_S,
    handshake_deadline_s: float = DEFAULT_HANDSHAKE_DEADLINE_S,
) -> int:
    """Main async orchestrator for the desktop adapter server.

    Returns 0 on clean exit, non-zero on failure or handshake timeout.
    """
    install_desktop_project_paths()

    loop = asyncio.get_running_loop()
    proc_done = asyncio.Event()

    server_sock, host, port = create_server_socket()
    token = secrets.token_hex(32)

    # 1. Output ready line before stdout redirection
    emit_ready_line(host, port, token)

    # 2. Wire lifecycle triggers: signals + stdin EOF watcher
    setup_signal_handlers(proc_done, loop)
    start_stdin_eof_watcher(proc_done, loop)

    conn: socket.socket | None = None
    watch_task: asyncio.Task | None = None
    try:
        # 3. Accept single client connection
        conn = await accept_client(server_sock, accept_timeout_s, proc_done)
        if conn is None:
            if proc_done.is_set():
                return 0
            logger.error(f"desktop_server: accept timed out ({accept_timeout_s:.1f}s)")
            return 1

        # 4. Monitor socket disconnect to unblock proc_done
        watch_task = asyncio.create_task(_watch_conn_closed(conn, proc_done))

        # 5. Hand over connection to _run_rpc_server_until_done
        handshake_ok = await _run_rpc_server_until_done(
            conn,
            token,
            handshake_deadline_s,
            proc_done,
            desktop=True,
        )
        return 0 if handshake_ok else 1
    finally:
        if watch_task is not None:
            watch_task.cancel()
            try:
                await watch_task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            server_sock.close()
        except OSError:
            pass


def main() -> int:
    """CLI synchronous entrypoint."""
    redirect_loguru_to_file("desktop.log")
    install_desktop_project_paths()
    accept_timeout = float(os.environ.get("RINCODE_ACCEPT_TIMEOUT", str(DEFAULT_ACCEPT_TIMEOUT_S)))
    handshake_deadline = float(os.environ.get("RINCODE_HANDSHAKE_TIMEOUT", str(DEFAULT_HANDSHAKE_DEADLINE_S)))
    code = 1
    try:
        code = asyncio.run(
            run_desktop_server(
                accept_timeout_s=accept_timeout,
                handshake_deadline_s=handshake_deadline,
            )
        )
    except (KeyboardInterrupt, SystemExit):
        code = 0
    except Exception:
        logger.exception("desktop_server fatal error")
        code = 1

    if lancedb_finalization_hazard():
        flush_and_hard_exit(code)
    return code


if __name__ == "__main__":
    sys.exit(main())
