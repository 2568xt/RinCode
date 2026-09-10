'use strict';

const { spawn } = require('node:child_process');
const path = require('node:path');

const { isMethodAllowed } = require('./allowlist.cjs');
const { getSourceRoot, getPythonExecutable } = require('./metadata.cjs');
const { RpcClient } = require('./rpc-client.cjs');

const READY_LINE_TIMEOUT_MS = 10000;
const MAX_STDOUT_BUFFER_BYTES = 65536; // 64 KiB bound while awaiting ready line
const SHUTDOWN_SIGTERM_TIMEOUT_MS = 1500;
const SHUTDOWN_SIGKILL_TIMEOUT_MS = 1000;
const SHUTDOWN_OVERALL_TIMEOUT_MS = 3500;

class BackendManager {
  /**
   * @param {object} [options]
   * @param {(event: { projectId: string, method: string, params: any }) => void} [options.onEvent]
   * @param {string} [options.sourceRoot]
   * @param {string} [options.pythonExecutable]
   */
  constructor(options = {}) {
    this.onEvent = options.onEvent || (() => {});
    this.customSourceRoot = options.sourceRoot;
    this.customPython = options.pythonExecutable;

    /** @type {{ id: string, name: string, path: string } | null} */
    this.activeProject = null;

    /** @type {{ state: 'starting' | 'ready' | 'stopped' | 'error', message?: string }} */
    this.status = { state: 'stopped' };

    /** @type {import('node:child_process').ChildProcess | null} */
    this.childProcess = null;

    /** @type {RpcClient | null} */
    this.rpcClient = null;

    this.isShuttingDown = false;
    /** @type {Promise<void> | null} */
    this.shutdownPromise = null;
    /** @type {Promise<any>} */
    this.connectQueue = Promise.resolve();
    this.generation = 0;
  }

  /**
   * Emits a status update event to the renderer and updates local state.
   *
   * @param {string} projectId
   * @param {{ state: 'starting' | 'ready' | 'stopped' | 'error', message?: string }} status
   */
  emitStatus(projectId, status) {
    this.status = status;
    try {
      this.onEvent({
        projectId,
        method: 'backend.status',
        params: status,
      });
    } catch {
      // Ignore delivery errors
    }
  }

  /**
   * Connects to a project runtime. Serializes concurrent calls to avoid interleaving.
   *
   * @param {{ id: string, name: string, path: string }} project
   * @returns {Promise<{ state: 'starting' | 'ready' | 'stopped' | 'error', message?: string }>}
   */
  async connect(project) {
    const next = this.connectQueue.catch(() => {}).then(() => this._connectInternal(project));
    this.connectQueue = next.catch(() => {});
    return next;
  }

  /**
   * @private
   * @param {{ id: string, name: string, path: string }} project
   */
  async _connectInternal(project) {
    if (!project || typeof project.id !== 'string' || typeof project.path !== 'string') {
      throw new Error('Invalid project configuration supplied to connect()');
    }

    // If already ready for this exact project, return current status
    if (
      this.activeProject &&
      this.activeProject.id === project.id &&
      this.status.state === 'ready' &&
      this.rpcClient &&
      !this.rpcClient.closed
    ) {
      return this.status;
    }

    // Cleanly tear down any active backend before launching the new one
    if (this.childProcess || this.rpcClient || this.status.state === 'starting' || this.status.state === 'ready') {
      await this.shutdown();
    }

    this.isShuttingDown = false;
    this.activeProject = project;
    const currentGeneration = ++this.generation;
    this.emitStatus(project.id, { state: 'starting' });

    const sourceRoot = this.customSourceRoot || getSourceRoot();
    const pythonExe = this.customPython || getPythonExecutable(sourceRoot);

    const envPythonPath = [sourceRoot, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter);
    const childEnv = {
      ...process.env,
      PYTHONPATH: envPythonPath,
      PYTHONUNBUFFERED: '1',
    };

    let stderrBuffer = '';

    const spawnPromise = new Promise((resolve, reject) => {
      let readyParsed = false;
      let stdoutBuffer = '';

      const timer = setTimeout(() => {
        if (!readyParsed) {
          reject(new Error(`Timed out waiting for Python backend ready line after ${READY_LINE_TIMEOUT_MS}ms`));
        }
      }, READY_LINE_TIMEOUT_MS);

      let child;
      try {
        child = spawn(pythonExe, ['-m', 'rincode.cli.desktop_server'], {
          cwd: project.path,
          env: childEnv,
          stdio: ['pipe', 'pipe', 'pipe'],
        });
      } catch (err) {
        clearTimeout(timer);
        reject(new Error(`Failed to invoke spawn for ${pythonExe}: ${err.message}`));
        return;
      }

      this.childProcess = child;
      child.stdin.on('error', () => {});

      child.stdout.setEncoding('utf-8');
      child.stdout.on('data', (chunk) => {
        // Prevent stale child process callbacks
        if (this.generation !== currentGeneration || this.childProcess !== child) {
          return;
        }

        // Once ready, ignore all subsequent stdout without accumulating
        if (readyParsed) {
          return;
        }

        stdoutBuffer += chunk;
        if (stdoutBuffer.length > MAX_STDOUT_BUFFER_BYTES) {
          stdoutBuffer = stdoutBuffer.slice(-MAX_STDOUT_BUFFER_BYTES);
        }

        let nlIndex = stdoutBuffer.indexOf('\n');
        while (nlIndex !== -1) {
          const line = stdoutBuffer.slice(0, nlIndex).trim();
          stdoutBuffer = stdoutBuffer.slice(nlIndex + 1);

          if (line.length > 0) {
            try {
              const data = JSON.parse(line);
              if (data && typeof data.address === 'string' && typeof data.token === 'string') {
                readyParsed = true;
                clearTimeout(timer);
                stdoutBuffer = ''; // Free buffer immediately
                resolve({ child, address: data.address, token: data.token });
                return;
              }
            } catch {
              // Non-ready stdout line before protocol initialization
            }
          }
          nlIndex = stdoutBuffer.indexOf('\n');
        }
      });

      child.stderr.setEncoding('utf-8');
      child.stderr.on('data', (chunk) => {
        if (this.generation !== currentGeneration || this.childProcess !== child) {
          return;
        }
        stderrBuffer += chunk;
        if (stderrBuffer.length > 8192) {
          stderrBuffer = stderrBuffer.slice(-8192);
        }
      });

      child.once('error', (err) => {
        clearTimeout(timer);
        if (this.generation !== currentGeneration) {
          return;
        }
        if (!readyParsed) {
          reject(new Error(`Failed to spawn Python process (${pythonExe}): ${err.message}`));
        }
      });

      child.once('exit', (code, signal) => {
        clearTimeout(timer);
        if (this.generation !== currentGeneration || this.childProcess !== child) {
          return; // Ignore callbacks from stale/killed child processes
        }

        if (!readyParsed) {
          const exitDetail = signal ? `signal ${signal}` : `code ${code}`;
          reject(new Error(`Python process exited prematurely (${exitDetail}): ${stderrBuffer.trim() || 'no stderr'}`));
        } else if (!this.isShuttingDown) {
          // Unexpected exit after ready
          const currentProjectId = this.activeProject ? this.activeProject.id : project.id;
          const status = code === 0
            ? { state: 'stopped', message: 'Backend process exited cleanly' }
            : { state: 'error', message: `Backend process crashed: ${stderrBuffer.trim() || 'code ' + code}` };
          this.emitStatus(currentProjectId, status);
        }
      });
    });

    let readyInfo;
    try {
      readyInfo = await spawnPromise;
    } catch (err) {
      await this.shutdown();
      const errStatus = { state: 'error', message: err.message };
      this.emitStatus(project.id, errStatus);
      return errStatus;
    }

    if (this.generation !== currentGeneration) {
      return { state: 'stopped', message: 'Superseded by subsequent connect' };
    }

    // Strictly validate address: only 127.0.0.1 with a valid integer port
    const parts = readyInfo.address.split(':');
    if (parts.length !== 2) {
      await this.shutdown();
      const errStatus = { state: 'error', message: `Invalid address format: ${readyInfo.address}` };
      this.emitStatus(project.id, errStatus);
      return errStatus;
    }

    const [host, portStr] = parts;
    const port = Number(portStr);
    if (host !== '127.0.0.1' || !Number.isInteger(port) || port < 1 || port > 65535) {
      await this.shutdown();
      const errStatus = {
        state: 'error',
        message: `Security rejection: backend bound to unauthorized address '${readyInfo.address}'. Only 127.0.0.1 is accepted.`,
      };
      this.emitStatus(project.id, errStatus);
      return errStatus;
    }

    // Create RPC client. Note: readyInfo.token is strictly internal and never exposed
    const client = new RpcClient({
      host,
      port,
      token: readyInfo.token,
      onNotification: (method, params) => {
        if (this.generation === currentGeneration) {
          this.onEvent({
            projectId: project.id,
            method,
            params,
          });
        }
      },
      onClose: (err) => {
        if (this.generation === currentGeneration && !this.isShuttingDown && this.status.state === 'ready') {
          this.emitStatus(project.id, {
            state: 'error',
            message: `RPC connection closed: ${err ? err.message : 'peer closed'}`,
          });
        }
      },
    });

    this.rpcClient = client;

    try {
      await client.ready();
      // Perform handshake system.hello within 5s per contract
      await client.handshake('0.1.0', 5000);
    } catch (err) {
      await this.shutdown();
      const errStatus = { state: 'error', message: `Backend handshake failed: ${err.message}` };
      this.emitStatus(project.id, errStatus);
      return errStatus;
    }

    if (this.generation !== currentGeneration) {
      return { state: 'stopped', message: 'Superseded by subsequent connect' };
    }

    const readyStatus = { state: 'ready' };
    this.emitStatus(project.id, readyStatus);
    return readyStatus;
  }

  /**
   * Invokes an RPC method on the active backend.
   *
   * @param {string} projectId
   * @param {string} method
   * @param {Record<string, unknown>} [params]
   * @returns {Promise<any>}
   */
  async rpc(projectId, method, params = {}) {
    if (!this.activeProject || this.activeProject.id !== projectId) {
      throw new Error(`Project '${projectId}' is not the currently active backend`);
    }

    if (!isMethodAllowed(method)) {
      throw new Error(`RPC method '${method}' is not allowed`);
    }

    if (this.status.state !== 'ready' || !this.rpcClient) {
      throw new Error(`Active backend is not ready (state: ${this.status.state})`);
    }

    return this.rpcClient.rpc(method, params);
  }

  /**
   * Cleanly and idempotently shuts down the active backend runtime.
   * Guaranteed to resolve within a bounded time even if child already exited or failed spawn.
   *
   * @returns {Promise<void>}
   */
  async shutdown() {
    if (this.shutdownPromise) {
      return this.shutdownPromise;
    }

    this.shutdownPromise = this._shutdownInternal().finally(() => {
      this.shutdownPromise = null;
    });
    return this.shutdownPromise;
  }

  /**
   * @private
   */
  async _shutdownInternal() {
    this.isShuttingDown = true;
    const project = this.activeProject;

    if (this.rpcClient) {
      try {
        this.rpcClient.close();
      } catch {
        // noop
      }
      this.rpcClient = null;
    }

    const child = this.childProcess;
    this.childProcess = null;

    // Check if child already exited or failed spawn
    const childAlreadyExited = !child ||
      child.exitCode !== null ||
      child.signalCode !== null ||
      !child.pid;

    if (!childAlreadyExited) {
      await new Promise((resolve) => {
        let finished = false;

        const complete = () => {
          if (!finished) {
            finished = true;
            clearTimeout(termTimer);
            clearTimeout(killTimer);
            clearTimeout(overallTimer);
            resolve();
          }
        };

        // Bounded final resolution guard
        const overallTimer = setTimeout(complete, SHUTDOWN_OVERALL_TIMEOUT_MS);

        child.once('exit', complete);
        child.once('error', complete);

        // Step 1: Close stdin pipe (sends EOF, triggers desktop_server clean exit)
        try {
          if (child.stdin && !child.stdin.destroyed) {
            child.stdin.end();
          }
        } catch {
          // noop
        }

        // Step 2: Bounded SIGTERM fallback
        const termTimer = setTimeout(() => {
          if (!finished) {
            try {
              child.kill('SIGTERM');
            } catch {
              // noop
            }
          }
        }, SHUTDOWN_SIGTERM_TIMEOUT_MS);

        // Step 3: Bounded SIGKILL fallback
        const killTimer = setTimeout(() => {
          if (!finished) {
            try {
              child.kill('SIGKILL');
            } catch {
              // noop
            }
          }
        }, SHUTDOWN_SIGTERM_TIMEOUT_MS + SHUTDOWN_SIGKILL_TIMEOUT_MS);
      });
    }

    if (project) {
      this.emitStatus(project.id, { state: 'stopped' });
    }

    this.activeProject = null;
    this.status = { state: 'stopped' };
  }
}

module.exports = {
  READY_LINE_TIMEOUT_MS,
  MAX_STDOUT_BUFFER_BYTES,
  SHUTDOWN_SIGTERM_TIMEOUT_MS,
  SHUTDOWN_SIGKILL_TIMEOUT_MS,
  SHUTDOWN_OVERALL_TIMEOUT_MS,
  BackendManager,
};
