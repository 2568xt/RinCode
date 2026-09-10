import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { ProjectStore } = require('../project-store.cjs');
const { SessionArchiveStore } = require('../session-archive-store.cjs');

function temporary(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rincode-library-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

test('remove registration including last project preserves source and history', (t) => {
  const dir = temporary(t);
  const projectDir = path.join(dir, 'source');
  const historyDir = path.join(dir, 'history');
  fs.mkdirSync(projectDir);
  fs.mkdirSync(historyDir);
  const source = path.join(projectDir, 'code.py');
  const history = path.join(historyDir, 'session.jsonl');
  fs.writeFileSync(source, 'source contents');
  fs.writeFileSync(history, 'saved history');
  const store = new ProjectStore(path.join(dir, 'app'));
  const a = store.addProject(projectDir);
  const b = store.addProject(path.join(dir, 'other'));
  assert.deepEqual(store.removeProject(a.id), [b]);
  assert.deepEqual(new ProjectStore(path.join(dir, 'app')).loadProjects(), [b]);
  assert.deepEqual(store.removeProject('unknown'), [b]);
  assert.deepEqual(store.removeProject(b.id), []);
  assert.equal(fs.readFileSync(source, 'utf8'), 'source contents');
  assert.equal(fs.readFileSync(history, 'utf8'), 'saved history');
});

test('archive is project-scoped and survives restart, removal, and reimport', (t) => {
  const dir = temporary(t);
  const projects = new ProjectStore(dir);
  const a = projects.addProject(path.join(dir, 'source'));
  const archives = new SessionArchiveStore(dir);
  assert.equal(archives.isArchived(a.id, 'tui:one'), false);
  archives.setArchived(a.id, 'tui:one', true);
  assert.equal(new SessionArchiveStore(dir).isArchived(a.id, 'tui:one'), true);
  assert.equal(archives.isArchived('other-project', 'tui:one'), false);
  projects.removeProject(a.id);
  const reimported = projects.addProject(path.join(dir, 'source'));
  assert.equal(reimported.id, a.id);
  assert.equal(new SessionArchiveStore(dir).isArchived(a.id, 'tui:one'), true);
  archives.setArchived(a.id, 'tui:one', false);
  assert.equal(new SessionArchiveStore(dir).isArchived(a.id, 'tui:one'), false);
  assert.deepEqual(fs.readdirSync(dir).sort(), ['projects.json', 'session-archives.json']);
});

for (const invalid of ['broken-json', '{}', '{"archives": {"a": "wrong"}}']) {
  test(`corrupt archives fail without overwriting: ${invalid}`, (t) => {
    const dir = temporary(t);
    const file = path.join(dir, 'session-archives.json');
    fs.writeFileSync(file, invalid);
    assert.throws(() => new SessionArchiveStore(dir).setArchived('a', 'tui:one', true));
    assert.equal(fs.readFileSync(file, 'utf8'), invalid);
  });
}

test('corrupt projects cannot be overwritten by registration removal', (t) => {
  const dir = temporary(t);
  const file = path.join(dir, 'projects.json');
  fs.writeFileSync(file, 'broken-json');
  assert.throws(() => new ProjectStore(dir).removeProject('a'));
  assert.equal(fs.readFileSync(file, 'utf8'), 'broken-json');
});

test('failed archive replacement keeps previous state and cleans temporary file', (t) => {
  const dir = temporary(t);
  const store = new SessionArchiveStore(dir);
  store.setArchived('a', 'tui:one', true);
  const file = path.join(dir, 'session-archives.json');
  const original = fs.readFileSync(file);
  t.mock.method(fs, 'renameSync', () => { throw new Error('simulated rename failure'); });
  assert.throws(() => store.setArchived('a', 'tui:one', false), /rename failure/);
  assert.deepEqual(fs.readFileSync(file), original);
  assert.deepEqual(fs.readdirSync(dir), ['session-archives.json']);
});
