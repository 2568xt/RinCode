import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { after, before, describe, it } from 'node:test';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { ProjectStore, makeProjectId, makeProjectName } = require('../project-store.cjs');
const { isMethodAllowed } = require('../allowlist.cjs');
const { getPythonExecutable, getSourceRoot } = require('../metadata.cjs');
const { BackendManager } = require('../backend-manager.cjs');

describe('Project Store & Identification', () => {
  let tempDir;

  before(() => {
    tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'rincode-test-store-'));
  });

  after(() => {
    fs.rmSync(tempDir, { recursive: true, force: true });
  });

  it('generates stable, deterministic project IDs', () => {
    const id1 = makeProjectId('/path/to/my-project');
    const id2 = makeProjectId('/path/to/my-project/');
    const id3 = makeProjectId('/path/to/my-project/../my-project');
    assert.equal(id1.length, 16);
    assert.equal(id1, id2);
    assert.equal(id1, id3);

    const diffId = makeProjectId('/different/path');
    assert.notEqual(id1, diffId);
  });

  it('derives human-readable project names from directory path', () => {
    assert.equal(makeProjectName('/Users/dev/workspace/frontend-app'), 'frontend-app');
    assert.equal(makeProjectName('/workspace'), 'workspace');
  });

  it('persists and retrieves projects without duplicates', () => {
    const store = new ProjectStore(tempDir);
    assert.deepEqual(store.loadProjects(), []);

    const projA = store.addProject('/fake/path/alpha');
    assert.equal(projA.name, 'alpha');
    assert.ok(projA.id);

    // Adding same project returns existing record
    const duplicate = store.addProject('/fake/path/alpha/');
    assert.equal(duplicate.id, projA.id);

    const projB = store.addProject('/fake/path/beta');
    assert.notEqual(projB.id, projA.id);

    // Persistence check with fresh store instance
    const store2 = new ProjectStore(tempDir);
    const loaded = store2.loadProjects();
    assert.equal(loaded.length, 2);
    assert.equal(store2.getProject(projA.id).name, 'alpha');
    assert.equal(store2.getProject(projB.id).name, 'beta');
    assert.equal(store2.getProject('nonexistent-id'), null);
  });
});

describe('Security & Allowlist Guardrails', () => {
  it('permits valid RPC operations defined in OpenRPC contract', () => {
    assert.ok(isMethodAllowed('session.list'));
    assert.ok(isMethodAllowed('session.create'));
    assert.ok(isMethodAllowed('session.resume'));
    assert.ok(isMethodAllowed('session.delete'));
    assert.ok(isMethodAllowed('turn.send'));
    assert.ok(isMethodAllowed('turn.subscribe'));
    assert.ok(isMethodAllowed('turn.cancel'));
    assert.ok(isMethodAllowed('confirm.respond'));
    assert.ok(isMethodAllowed('clarify.respond'));
    assert.ok(isMethodAllowed('system.hello'));
    assert.ok(isMethodAllowed('system.ping'));
    assert.ok(isMethodAllowed('system.version'));
    assert.equal(isMethodAllowed('config.get'), false);
    assert.equal(isMethodAllowed('config.set'), false);
  });

  it('strictly rejects unlisted or malicious methods', () => {
    assert.equal(isMethodAllowed('process.exit'), false);
    assert.equal(isMethodAllowed('child_process.exec'), false);
    assert.equal(isMethodAllowed('os.system'), false);
    assert.equal(isMethodAllowed('__proto__'), false);
    assert.equal(isMethodAllowed(''), false);
    assert.equal(isMethodAllowed(null), false);
    assert.equal(isMethodAllowed(undefined), false);
    assert.equal(isMethodAllowed(123), false);
  });
});

describe('Metadata & Python Resolution', () => {
  const originalEnv = { ...process.env };

  after(() => {
    process.env = originalEnv;
  });

  it('honors RINCODE_PYTHON when specified', () => {
    process.env.RINCODE_PYTHON = '/custom/bin/python-test';
    assert.equal(getPythonExecutable(), '/custom/bin/python-test');
  });

  it('honors RINCODE_SOURCE_ROOT when specified', () => {
    const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'rincode-test-root-'));
    process.env.RINCODE_SOURCE_ROOT = tempRoot;
    assert.equal(getSourceRoot(), tempRoot);
    fs.rmSync(tempRoot, { recursive: true, force: true });
  });
});

describe('Backend Lifecycle with Mock Runtime', () => {
  let mockServerScript;
  let tempDir;

  before(() => {
    tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'rincode-test-backend-'));

    mockServerScript = path.join(tempDir, 'mock_server.cjs');
    fs.writeFileSync(
      mockServerScript,
      `
const net = require('node:net');

const server = net.createServer((sock) => {
  sock.setEncoding('utf-8');
  let firstLine = true;
  let buf = '';

  sock.on('data', (chunk) => {
    buf += chunk;
    let nl = buf.indexOf('\\n');
    while (nl !== -1) {
      const line = buf.slice(0, nl);
      buf = buf.slice(nl + 1);

      if (firstLine) {
        firstLine = false;
        if (line !== 'secret-token') {
          sock.destroy();
          return;
        }
      } else if (line.length > 0) {
        try {
          const req = JSON.parse(line);
          if (req.method === 'system.hello') {
            sock.write(JSON.stringify({
              jsonrpc: '2.0',
              id: req.id,
              result: { server_version: '0.1.0', server_capabilities: ['sessions'] }
            }) + '\\n');
          } else if (req.method === 'session.list') {
            sock.write(JSON.stringify({
              jsonrpc: '2.0',
              method: 'event',
              params: {
                subscription_id: 'sub-42',
                event: { type: 'token.delta', payload: { text: 'chunk' } }
              }
            }) + '\\n');

            sock.write(JSON.stringify({
              jsonrpc: '2.0',
              id: req.id,
              result: { sessions: [{ id: 's1', title: 'Main' }] }
            }) + '\\n');
          }
        } catch {
          // ignore
        }
      }
      nl = buf.indexOf('\\n');
    }
  });
});

server.listen(0, '127.0.0.1', () => {
  const port = server.address().port;
  process.stdout.write(JSON.stringify({ address: '127.0.0.1:' + port, token: 'secret-token' }) + '\\n');
});

process.stdin.on('end', () => {
  server.close(() => process.exit(0));
});
process.stdin.resume();
      `,
      'utf-8'
    );
  });

  after(() => {
    fs.rmSync(tempDir, { recursive: true, force: true });
  });

  it('rejects RPC calls when backend is stopped or not ready using assert.rejects', async () => {
    const manager = new BackendManager();
    await assert.rejects(
      async () => {
        await manager.rpc('proj-1', 'session.list');
      },
      /not the currently active/
    );
  });

  it('rejects disallowed RPC calls even if project matches', async () => {
    const manager = new BackendManager();
    manager.activeProject = { id: 'p1', name: 'proj', path: tempDir };
    manager.status = { state: 'ready' };
    manager.rpcClient = {};

    await assert.rejects(
      async () => {
        await manager.rpc('p1', 'unauthorized.method');
      },
      /not allowed/
    );
  });

  it('manages full connection, RPC, event forwarding, and clean shutdown lifecycle', async () => {
    const events = [];
    const manager = new BackendManager({
      onEvent: (evt) => events.push(evt),
    });

    const wrapperBin = path.join(tempDir, 'runner.js');
    fs.writeFileSync(
      wrapperBin,
      `#!/usr/bin/env node\nrequire(${JSON.stringify(mockServerScript)});\n`,
      'utf-8'
    );
    fs.chmodSync(wrapperBin, 0o755);

    manager.customPython = wrapperBin;

    const project = { id: 'p-test', name: 'test-project', path: tempDir };
    const connectStatus = await manager.connect(project);
    assert.equal(connectStatus.state, 'ready');

    assert.ok(events.some((e) => e.method === 'backend.status' && e.params.state === 'starting'));
    assert.ok(events.some((e) => e.method === 'backend.status' && e.params.state === 'ready'));

    const listResult = await manager.rpc('p-test', 'session.list', {});
    assert.deepEqual(listResult, { sessions: [{ id: 's1', title: 'Main' }] });

    const streamEvent = events.find((e) => e.method === 'event');
    assert.ok(streamEvent);
    assert.equal(streamEvent.projectId, 'p-test');
    assert.equal(streamEvent.params.subscription_id, 'sub-42');
    assert.equal(streamEvent.params.event.type, 'token.delta');

    await manager.shutdown();
    assert.equal(manager.status.state, 'stopped');
    assert.ok(events.some((e) => e.method === 'backend.status' && e.params.state === 'stopped'));
  });

  it('regression: shutdown does not hang when child process has already exited', async () => {
    const manager = new BackendManager();
    const alreadyExitedChild = spawn(process.execPath, ['-e', 'process.exit(0)']);

    // Wait for the short process to fully exit
    await new Promise((resolve) => alreadyExitedChild.once('exit', resolve));
    assert.notEqual(alreadyExitedChild.exitCode, null);

    manager.childProcess = alreadyExitedChild;
    manager.activeProject = { id: 'p-exited', name: 'exited', path: tempDir };
    manager.status = { state: 'ready' };

    const start = Date.now();
    await manager.shutdown();
    const duration = Date.now() - start;

    assert.equal(manager.status.state, 'stopped');
    assert.equal(manager.childProcess, null);
    // Should resolve immediately without waiting on timeouts
    assert.ok(duration < 200, `Shutdown took too long: ${duration}ms`);
  });

  it('regression: shutdown does not hang when spawn failed', async () => {
    const manager = new BackendManager({
      pythonExecutable: '/nonexistent/bin/python-fail-spawn',
    });

    const project = { id: 'p-fail', name: 'fail', path: tempDir };
    const status = await manager.connect(project);
    assert.equal(status.state, 'error');

    const start = Date.now();
    await manager.shutdown();
    const duration = Date.now() - start;

    assert.equal(manager.status.state, 'stopped');
    assert.ok(duration < 200, `Shutdown after failed spawn took too long: ${duration}ms`);
  });

  it('serializes concurrent connect calls safely', async () => {
    const wrapperBin = path.join(tempDir, 'runner.js');
    const manager = new BackendManager({
      pythonExecutable: wrapperBin,
    });

    const projA = { id: 'p-a', name: 'proj-a', path: tempDir };
    const projB = { id: 'p-b', name: 'proj-b', path: tempDir };

    // Fire both concurrently
    const [resA, resB] = await Promise.all([
      manager.connect(projA),
      manager.connect(projB),
    ]);

    assert.ok(resA);
    assert.ok(resB);
    assert.equal(manager.activeProject.id, 'p-b');
    await manager.shutdown();
  });
});
