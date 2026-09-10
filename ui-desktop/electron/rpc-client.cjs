'use strict';

const net = require('node:net');

const MAX_FRAME_BYTES = 1024 * 1024; // 1 MiB limit per JSON frame
const DEFAULT_RPC_TIMEOUT_MS = 60000; // 60s default timeout for RPC requests
const HANDSHAKE_TIMEOUT_MS = 5000; // 5s timeout for system.hello handshake
const CONNECT_TIMEOUT_MS = 5000; // 5s timeout for initial TCP connection

class RpcError extends Error {
  /**
   * @param {string} message
   * @param {number} [code]
   * @param {unknown} [data]
   */
  constructor(message, code, data) {
    super(message);
    this.name = 'RpcError';
    this.code = code;
    this.data = data;
  }
}

class RpcClient {
  /**
   * @param {object} options
   * @param {string} options.host
   * @param {number} options.port
   * @param {string} options.token Auth token sent on first line (never logged or exposed in errors)
   * @param {(method: string, params: any) => void} [options.onNotification]
   * @param {(err: Error) => void} [options.onClose]
   * @param {(msg: string) => void} [options.warn]
   */
  constructor(options) {
    this.host = options.host;
    this.port = options.port;
    this.token = options.token;
    this.onNotification = options.onNotification || (() => {});
    this.onClose = options.onClose || (() => {});
    this.warn = options.warn || (() => {});

    this.nextId = 1;
    this.readBuffer = '';
    this.writeQueue = Promise.resolve();
    this.closed = false;
    this.connected = false;

    /** @type {Map<number | string, { resolve: (val: any) => void, reject: (err: Error) => void, timer: NodeJS.Timeout }>} */
    this.pending = new Map();

    this.socket = net.createConnection({ host: this.host, port: this.port });
    this.socket.setEncoding('utf-8');

    this.connectPromise = new Promise((resolve, reject) => {
      const connectTimer = setTimeout(() => {
        this.socket.off('connect', onConnect);
        this.socket.off('error', onError);
        const err = new Error(`Connection to ${this.host}:${this.port} timed out after ${CONNECT_TIMEOUT_MS}ms`);
        this.failAll(err);
        try {
          this.socket.destroy();
        } catch {
          // noop
        }
        reject(err);
      }, CONNECT_TIMEOUT_MS);

      this.socket.once('close', () => {
        clearTimeout(connectTimer);
        reject(new Error('RPC connection closed before ready'));
      });

      const onConnect = () => {
        clearTimeout(connectTimer);
        this.connected = true;
        this.socket.off('error', onError);
        // First line sent must be the authentication token, followed by newline.
        // Token is strictly kept internal and never exposed in logs or errors.
        if (this.token) {
          this.socket.write(this.token + '\n');
        }
        resolve();
      };

      const onError = (err) => {
        clearTimeout(connectTimer);
        this.socket.off('connect', onConnect);
        const sanitizedErr = new Error(`RPC connection error: ${err.message}`);
        this.failAll(sanitizedErr);
        reject(sanitizedErr);
      };

      this.socket.once('connect', onConnect);
      this.socket.once('error', onError);
    });

    // Callers may close before awaiting ready (for example oversized requests).
    this.connectPromise.catch(() => {});

    this.socket.on('data', (chunk) => {
      const text = typeof chunk === 'string' ? chunk : chunk.toString('utf-8');
      this.readBuffer += text;

      // Measure byte length in UTF-8
      if (Buffer.byteLength(this.readBuffer, 'utf-8') > MAX_FRAME_BYTES * 2) {
        this.warn(`Incoming read buffer exceeded ${MAX_FRAME_BYTES * 2} bytes without newline. Closing connection.`);
        this.failAll(new Error('rpc-client: frame size limit exceeded'));
        this.socket.destroy();
        return;
      }

      this.drainBuffer();
    });

    this.socket.on('end', () => {
      this.failAll(new Error('RPC socket closed by server'));
    });

    this.socket.on('close', () => {
      this.failAll(new Error('RPC socket closed'));
    });

    this.socket.on('error', (err) => {
      this.failAll(new Error(`RPC socket error: ${err.message}`));
    });
  }

  /**
   * Waits for the TCP connection to be established.
   * @returns {Promise<void>}
   */
  ready() {
    return this.connectPromise;
  }

  drainBuffer() {
    let nl = this.readBuffer.indexOf('\n');
    while (nl !== -1) {
      const line = this.readBuffer.slice(0, nl).trim();
      this.readBuffer = this.readBuffer.slice(nl + 1);
      if (line.length > 0) {
        this.handleFrame(line);
      }
      nl = this.readBuffer.indexOf('\n');
    }
  }

  /**
   * @param {string} line
   */
  handleFrame(line) {
    // Measure UTF-8 byte length
    const byteLength = Buffer.byteLength(line, 'utf-8');
    if (byteLength > MAX_FRAME_BYTES) {
      this.warn(`Oversized frame (${byteLength} bytes) dropped`);
      return;
    }

    let frame;
    try {
      frame = JSON.parse(line);
    } catch {
      this.warn(`Malformed JSON-RPC frame ignored: ${line.slice(0, 100)}`);
      return;
    }

    if (!frame || typeof frame !== 'object') {
      this.warn('Non-object frame ignored');
      return;
    }

    // Notification frame (no id, has method)
    if (frame.id === undefined && typeof frame.method === 'string') {
      try {
        this.onNotification(frame.method, frame.params);
      } catch (err) {
        this.warn(`Error handling notification ${frame.method}: ${err.message}`);
      }
      return;
    }

    // Response frame (has id)
    const id = frame.id;
    if (id === undefined || id === null) {
      this.warn('Response frame has no valid id');
      return;
    }

    const pending = this.pending.get(id);
    if (!pending) {
      this.warn(`Response received for unknown request id ${String(id)}`);
      return;
    }

    this.pending.delete(id);
    clearTimeout(pending.timer);

    if (frame.error) {
      const errObj = frame.error;
      const errMsg = typeof errObj === 'string' ? errObj : errObj.message || 'RPC execution error';
      const errCode = typeof errObj === 'object' ? errObj.code : -32603;
      const errData = typeof errObj === 'object' ? errObj.data : undefined;
      pending.reject(new RpcError(errMsg, errCode, errData));
    } else {
      pending.resolve(frame.result);
    }
  }

  /**
   * @param {string} frame
   * @returns {Promise<void>}
   */
  async writeFrame(frame) {
    const byteLength = Buffer.byteLength(frame, 'utf-8');
    if (byteLength > MAX_FRAME_BYTES) {
      throw new Error(`rpc-client: outgoing frame ${byteLength} bytes exceeds ${MAX_FRAME_BYTES} limit`);
    }

    const prev = this.writeQueue;
    this.writeQueue = (async () => {
      await prev;
      if (this.closed) {
        throw new Error('rpc-client: connection is closed');
      }
      if (!this.connected) {
        await this.connectPromise;
      }
      await new Promise((resolve, reject) => {
        this.socket.write(frame, 'utf-8', (err) => {
          if (err) {
            reject(new Error(`Socket write failed: ${err.message}`));
          } else {
            resolve();
          }
        });
      });
    })();
    return this.writeQueue;
  }

  /**
   * Invokes an RPC method and awaits the result.
   *
   * @template T
   * @param {string} method
   * @param {Record<string, unknown>} [params]
   * @param {number} [timeoutMs]
   * @returns {Promise<T>}
   */
  async rpc(method, params = {}, timeoutMs = DEFAULT_RPC_TIMEOUT_MS) {
    if (this.closed) {
      throw new Error('rpc-client: connection is closed');
    }

    const id = this.nextId++;
    const req = {
      jsonrpc: '2.0',
      id,
      method,
      params,
    };
    const frame = JSON.stringify(req) + '\n';
    const byteLength = Buffer.byteLength(frame, 'utf-8');
    if (byteLength > MAX_FRAME_BYTES) {
      throw new Error(`rpc-client: outgoing frame ${byteLength} bytes exceeds ${MAX_FRAME_BYTES} limit`);
    }

    return new Promise((resolve, reject) => {
      if (this.closed) {
        reject(new Error('rpc-client: connection is closed'));
        return;
      }

      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`RPC request '${method}' timed out after ${timeoutMs}ms`));
      }, timeoutMs);

      this.pending.set(id, {
        resolve,
        reject,
        timer,
      });

      this.writeFrame(frame).catch((err) => {
        const p = this.pending.get(id);
        if (p) {
          clearTimeout(p.timer);
          this.pending.delete(id);
          p.reject(err);
        }
      });
    });
  }

  /**
   * Performs the initial system.hello handshake per contract.
   *
   * @param {string} [clientVersion='0.1.0']
   * @param {number} [timeoutMs=HANDSHAKE_TIMEOUT_MS]
   * @returns {Promise<any>}
   */
  async handshake(clientVersion = '0.1.0', timeoutMs = HANDSHAKE_TIMEOUT_MS) {
    return this.rpc(
      'system.hello',
      {
        client_version: clientVersion,
        client_capabilities: ['jsonrpc-2.0', 'subscriptions', 'attachments', 'sessions', 'confirm'],
      },
      timeoutMs
    );
  }

  /**
   * Fails and rejects all currently pending requests and marks client closed.
   *
   * @param {Error} err
   */
  failAll(err) {
    if (this.closed) {
      return;
    }
    this.closed = true;

    for (const [, p] of this.pending) {
      clearTimeout(p.timer);
      p.reject(err);
    }
    this.pending.clear();

    try {
      this.onClose(err);
    } catch {
      // Ignore errors from onClose callback
    }
  }

  /**
   * Closes the client and tears down the socket connection.
   */
  close() {
    this.failAll(new Error('rpc-client: closed by caller'));
    try {
      this.socket.end();
    } catch {
      // noop
    }
    try {
      this.socket.destroy();
    } catch {
      // noop
    }
  }

  /**
   * Current number of pending requests.
   * @returns {number}
   */
  pendingCount() {
    return this.pending.size;
  }
}

module.exports = {
  MAX_FRAME_BYTES,
  DEFAULT_RPC_TIMEOUT_MS,
  HANDSHAKE_TIMEOUT_MS,
  CONNECT_TIMEOUT_MS,
  RpcError,
  RpcClient,
};
