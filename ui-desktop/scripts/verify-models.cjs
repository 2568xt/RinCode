// Opt-in: a short real model call in a temporary project; global config must stay unchanged.
const { BackendManager } = require('../electron/backend-manager.cjs');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '../..');
const python = process.env.RINCODE_PYTHON || path.join(root, '.venv/bin/python');
const configPath = execFileSync(python, ['-c', 'from rincode.config.loader import get_config_path; print(get_config_path())'], { cwd: root, encoding: 'utf8' }).trim();
const configHash = () => crypto.createHash('sha256').update(fs.readFileSync(configPath)).digest('hex');
const before = configHash();
const qa = fs.mkdtempSync(path.join(os.tmpdir(), 'rincode-model-verify-'));
const projectA = { id: 'model-a', name: 'Model check', path: path.join(qa, 'a') };
const projectB = { id: 'model-b', name: 'Isolation check', path: path.join(qa, 'b') };
fs.mkdirSync(projectA.path); fs.mkdirSync(projectB.path);
let finish;
const terminal = new Promise(resolve => { finish = resolve; });
let text = '';
const manager = new BackendManager({ sourceRoot: root, pythonExecutable: python, onEvent: event => {
  const e = event.method === 'event' && event.params.event;
  if (e?.type === 'token.delta') text += e.payload.text;
  if (e && ['message.complete', 'error'].includes(e.type)) finish(e);
} });
(async () => {
  try {
    await manager.connect(projectA);
    const rpc = (method, params = {}) => manager.rpc(projectA.id, method, params);
    const original = await rpc('desktop.model.options');
    const provider = original.providers.find(p => p.configured && p.slug === original.provider && p.models.some(m => m !== original.model));
    assert.ok(provider, 'A configured alternative model is required for this opt-in test');
    const model = provider.models.find(m => m !== original.model);
    const switched = await rpc('desktop.model.select', { model, provider: provider.slug });
    assert.equal(switched.model, model);
    assert.equal((await rpc('desktop.model.options')).model, model);
    console.log('Live model switched:', original.model, '->', model);
    const session = await rpc('session.create');
    assert.equal((await rpc('desktop.model.options')).model, model);
    await rpc('turn.subscribe', { session_key: session.session_id });
    await rpc('turn.send', { session_key: session.session_id, content: '只回复 MODEL_SWITCH_OK，不调用工具，不修改文件。' });
    await assert.rejects(rpc('desktop.model.select', { model: original.model, provider: original.provider }), e => e.code === -32009);
    const end = await Promise.race([terminal, new Promise((_, reject) => setTimeout(() => reject(Error('Model reply timed out')), 90000).unref())]);
    assert.equal(end.type, 'message.complete');
    assert.match(text, /MODEL_SWITCH_OK/);
    console.log('Real response after switch and active-turn rejection passed');
    await rpc('session.resume', { session_id: session.session_id });
    assert.equal((await rpc('desktop.model.options')).model, model);
    const switchedBack = await rpc('desktop.model.select', { model: original.model, provider: original.provider });
    assert.equal(switchedBack.model, original.model);
    await assert.rejects(rpc('desktop.model.select', { model: '__not_a_configured_model__', provider: original.provider }));
    assert.equal((await rpc('desktop.model.options')).model, original.model);
    await rpc('desktop.model.select', { model, provider: provider.slug });
    await manager.connect(projectB);
    assert.equal((await manager.rpc(projectB.id, 'desktop.model.options')).model, original.model);
    await manager.connect(projectA);
    assert.equal((await rpc('desktop.model.options')).model, original.model);
    assert.equal(configHash(), before);
    console.log('Create/resume consistency, rejected invalid choice, project isolation, reconnect default and unchanged global config passed');
  } finally {
    await manager.shutdown();
    assert.equal(configHash(), before, 'Global configuration was modified');
  }
})().catch(e => { console.error(e.message); process.exitCode = 1; });
