import assert from 'node:assert/strict';
import net from 'node:net';
import { after, before, describe, it } from 'node:test';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { RpcClient, RpcError, MAX_FRAME_BYTES } = require('../rpc-client.cjs');

describe('RPC Client Transport', () => {
  let server;
  let serverPort;
  let currentAuthHandler = null;
  let currentServerHandler = null;
  const activeSockets = new Set();

  before(async () => {
    server = net.createServer((sock) => {
      activeSockets.add(sock);
      sock.setEncoding('utf-8');
      let isFirstLine = true;
      let buf = '';

      sock.on('data', (chunk) => {
        buf += chunk;
        let nl = buf.indexOf('\n');
        while (nl !== -1) {
          const line = buf.slice(0, nl).trim();
          buf = buf.slice(nl + 1);

          if (isFirstLine) {
            isFirstLine = false;
            sock.authToken = line;
            if (currentAuthHandler) {
              currentAuthHandler(sock, line);
            }
          } else if (line.length > 0) {
            if (currentServerHandler) {
              currentServerHandler(sock, line);
            }
          }
          nl = buf.indexOf('\n');
        }
      });

      sock.once('close', () => {
        activeSockets.delete(sock);
      });
    });

    await new Promise((resolve) => {
      server.listen(0, '127.0.0.1', () => {
        serverPort = server.address().port;
        resolve();
      });
    });
  });

  after(async () => {
    for (const sock of activeSockets) {
      try {
        sock.destroy();
      } catch {
        // noop
      }
    }
    activeSockets.clear();
    await new Promise((resolve) => server.close(resolve));
  });

  it('sends auth token as the very first line over TCP socket per socket instance', async () => {
    let capturedToken = null;
    currentAuthHandler = (_sock, token) => {
      capturedToken = token;
    };

    const client = new RpcClient({
      host: '127.0.0.1',
      port: serverPort,
      token: 'secret-token-12345',
    });

    await client.ready();
    await new Promise((r) => setTimeout(r, 50));

    assert.equal(capturedToken, 'secret-token-12345');
    client.close();
    currentAuthHandler = null;
  });

  it('performs JSON-RPC 2.0 request and response exchange', async () => {
    currentServerHandler = (sock, line) => {
      const parsed = JSON.parse(line);
      if (parsed.method === 'system.ping') {
        sock.write(JSON.stringify({
          jsonrpc: '2.0',
          id: parsed.id,
          result: { pong: true, timestamp: 123456 },
        }) + '\n');
      }
    };

    const client = new RpcClient({
      host: '127.0.0.1',
      port: serverPort,
      token: 'test-token',
    });

    const res = await client.rpc('system.ping', {});
    assert.deepEqual(res, { pong: true, timestamp: 123456 });
    client.close();
    currentServerHandler = null;
  });

  it('performs handshake system.hello per contract specifications', async () => {
    currentServerHandler = (sock, line) => {
      const parsed = JSON.parse(line);
      if (parsed.method === 'system.hello') {
        assert.equal(parsed.params.client_version, '0.1.0');
        assert.ok(Array.isArray(parsed.params.client_capabilities));
        sock.write(JSON.stringify({
          jsonrpc: '2.0',
          id: parsed.id,
          result: {
            server_version: '0.1.0',
            server_capabilities: ['jsonrpc-2.0', 'subscriptions'],
          },
        }) + '\n');
      }
    };

    const client = new RpcClient({
      host: '127.0.0.1',
      port: serverPort,
      token: 'test-token-handshake',
    });

    const res = await client.handshake('0.1.0', 2000);
    assert.equal(res.server_version, '0.1.0');
    client.close();
    currentServerHandler = null;
  });

  it('forwards server notifications via onNotification', async () => {
    let capturedMethod = null;
    let capturedParams = null;

    const client = new RpcClient({
      host: '127.0.0.1',
      port: serverPort,
      token: 'test-token-notif',
      onNotification: (method, params) => {
        capturedMethod = method;
        capturedParams = params;
      },
    });

    currentServerHandler = (sock, line) => {
      const parsed = JSON.parse(line);
      if (parsed.method === 'system.ping') {
        // First send notification frame, then response frame
        sock.write(JSON.stringify({
          jsonrpc: '2.0',
          method: 'confirm.request',
          params: { request_id: 'req-1', message: 'Delete session?' },
        }) + '\n');

        sock.write(JSON.stringify({
          jsonrpc: '2.0',
          id: parsed.id,
          result: { pong: true },
        }) + '\n');
      }
    };

    await client.rpc('system.ping', {});
    assert.equal(capturedMethod, 'confirm.request');
    assert.deepEqual(capturedParams, { request_id: 'req-1', message: 'Delete session?' });
    client.close();
    currentServerHandler = null;
  });

  it('handles JSON-RPC error responses with RpcError', async () => {
    currentServerHandler = (sock, line) => {
      const parsed = JSON.parse(line);
      sock.write(JSON.stringify({
        jsonrpc: '2.0',
        id: parsed.id,
        error: { code: -32601, message: 'Method not found', data: { detail: 'none' } },
      }) + '\n');
    };

    const client = new RpcClient({
      host: '127.0.0.1',
      port: serverPort,
      token: 'test-token-err',
    });

    await assert.rejects(
      async () => {
        await client.rpc('invalid.method', {});
      },
      (err) => {
        assert.ok(err instanceof RpcError);
        assert.equal(err.code, -32601);
        assert.equal(err.message, 'Method not found');
        return true;
      }
    );
    client.close();
    currentServerHandler = null;
  });

  it('rejects pending requests when RPC request times out', async () => {
    currentServerHandler = () => {
      // Intentionally do not respond to cause timeout
    };

    const client = new RpcClient({
      host: '127.0.0.1',
      port: serverPort,
      token: 'test-token-timeout',
    });

    await assert.rejects(
      async () => {
        await client.rpc('slow.method', {}, 100);
      },
      /timed out after 100ms/
    );

    assert.equal(client.pendingCount(), 0);
    client.close();
    currentServerHandler = null;
  });

  it('rejects all pending requests when socket is closed or disconnected', async () => {
    currentServerHandler = (sock) => {
      // Force destroy socket while client has pending request
      sock.destroy();
    };

    const client = new RpcClient({
      host: '127.0.0.1',
      port: serverPort,
      token: 'test-token-disconnect',
    });

    await assert.rejects(
      async () => {
        await client.rpc('session.list', {});
      },
      /RPC socket closed|connection is closed/
    );

    assert.equal(client.pendingCount(), 0);
    client.close();
    currentServerHandler = null;
  });

  it('enforces bounded frame limits on outgoing frames based on UTF-8 bytes', async () => {
    const client = new RpcClient({
      host: '127.0.0.1',
      port: serverPort,
      token: 'test-token-limit',
    });

    const hugePayload = 'x'.repeat(MAX_FRAME_BYTES + 10);
    await assert.rejects(
      async () => {
        await client.rpc('session.create', { data: hugePayload });
      },
      /exceeds 1048576 limit/
    );

    client.close();
  });
});
