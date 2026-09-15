import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

function compile(entry, require = createRequire(import.meta.url)) {
  const compiled = buildSync({
    stdin: { contents: entry, resolveDir: fileURLToPath(new URL('../..', import.meta.url)) },
    bundle: true, format: 'cjs', platform: 'node', packages: 'external', write: false,
  }).outputFiles[0].text;
  const module = { exports: {} };
  new Function('module', 'exports', 'require', compiled)(module, module.exports, require);
  return module.exports;
}

test('composer displays the draft supplied by its conversation context', () => {
  const { Composer } = compile("export { Composer } from './src/components/Composer';");
  const render = value => renderToStaticMarkup(React.createElement(Composer, {
    value, onChange() {}, onSend() {}, onCancel() {}, isTurnRunning: false, disabled: false, project: null,
  }));
  assert.match(render('只属于会话 A'), /只属于会话 A<\/textarea>/);
  assert.doesNotMatch(render(''), /只属于会话 A/);
});

test('drafts survive session and project switches, and sending clears only the captured draft', () => {
  let state;
  const require = createRequire(import.meta.url);
  const { useComposerDraft } = compile("export { useComposerDraft } from './src/hooks/useComposerDraft';", name => {
    if (name !== 'react') return require(name);
    return { useState(initial) {
      state ??= typeof initial === 'function' ? initial() : initial;
      return [state, update => { state = typeof update === 'function' ? update(state) : update; }];
    } };
  });
  const read = (project, session) => useComposerDraft(project, session);
  read('project A', 'session A').setDraft('A 草稿');
  assert.equal(read('project A', 'session B').draft, '');
  read('project A', 'session B').setDraft('B 草稿');
  assert.equal(read('project A', 'session A').draft, 'A 草稿');
  assert.equal(read('project B', 'session A').draft, '');
  read('project B', 'session A').setDraft('另一项目');
  assert.equal(read('project A', null).draft, '');
  read('project A', null).setDraft('首次发送');
  const initialDraft = read('project A', null);
  assert.equal(read('project A', 'new session').draft, '');
  initialDraft.clearDraft();
  assert.equal(read('project A', null).draft, '');
  read('project A', 'session B').clearDraft();
  assert.equal(read('project A', 'session B').draft, '');
  assert.equal(read('project A', 'session A').draft, 'A 草稿');
  assert.equal(read('project B', 'session A').draft, '另一项目');
});
