import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';

const require = createRequire(import.meta.url);
const { readProjectHistory } = require('../project-history.cjs');

test('history subprocess returns missing project without creating state', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rincode-history-'));
  const before = process.env.RINCODE_HOME;
  process.env.RINCODE_HOME = path.join(dir, 'state');
  try {
    const result = await readProjectHistory([{ id: 'project', path: path.join(dir, 'project') }]);
    assert.deepEqual(result, { project: { sessions: [] } });
    assert.equal(fs.existsSync(process.env.RINCODE_HOME), false);
  } finally {
    if (before === undefined) delete process.env.RINCODE_HOME;
    else process.env.RINCODE_HOME = before;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('process failures expose a safe actionable error', async () => {
  const before = process.env.RINCODE_PYTHON;
  process.env.RINCODE_PYTHON = '/nonexistent/private-diagnostic-python';
  try {
    await assert.rejects(readProjectHistory([{ id: 'a', path: '/tmp/a' }]), (error) => {
      assert.match(error.message, /Python/);
      assert.doesNotMatch(error.message, /private-diagnostic/);
      return true;
    });
  } finally {
    if (before === undefined) delete process.env.RINCODE_PYTHON;
    else process.env.RINCODE_PYTHON = before;
  }
});

test('query reaches Python and searches saved assistant body without writes', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rincode-search-'));
  const before = process.env.RINCODE_HOME;
  process.env.RINCODE_HOME = path.join(dir, 'state');
  try {
    const project = path.join(dir, 'project');
    fs.mkdirSync(project);
    const canonical = fs.realpathSync(project);
    const digest = createHash('sha256').update(canonical).digest('hex').slice(0, 12);
    const sessions = path.join(process.env.RINCODE_HOME, 'projects', `project-${digest}`, 'sessions', 'tui');
    fs.mkdirSync(sessions, { recursive: true });
    const file = path.join(sessions, 'one.jsonl');
    fs.writeFileSync(file, [
      { _type: 'metadata', key: 'tui:one', metadata: { title: 'Notes' }, created_at: '2026-09-11T00:00:00', updated_at: '2026-09-11T00:00:00' },
      { role: 'user', content: 'hello' },
      { role: 'assistant', content: '后续消息中的中文答案' },
    ].map(row => JSON.stringify(row)).join('\n') + '\n');
    const original = fs.readFileSync(file);
    const projects = [{ id: 'a', path: project }];
    const match = await readProjectHistory(projects, '中文答案');
    assert.equal(match.a.sessions.length, 1);
    assert.match(match.a.sessions[0].preview, /中文答案/);
    assert.equal((await readProjectHistory(projects, 'absent')).a.sessions.length, 0);
    assert.deepEqual(fs.readFileSync(file), original);
  } finally {
    if (before === undefined) delete process.env.RINCODE_HOME;
    else process.env.RINCODE_HOME = before;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
