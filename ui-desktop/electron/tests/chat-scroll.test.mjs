import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { buildSync } from 'esbuild';

const compiled = buildSync({
  stdin: {
    contents: "export { ChatArea } from './src/components/ChatArea';",
    resolveDir: fileURLToPath(new URL('../..', import.meta.url)),
  },
  bundle: true, format: 'cjs', platform: 'node', packages: 'external', write: false,
  loader: { '.css': 'empty' },
}).outputFiles[0].text;

// Exercise component refs, scroll handlers and effects with plain scroll metrics.
// No browser, layout engine, timers or model requests are needed.
function conversation() {
  const require = createRequire(import.meta.url);
  const refs = [];
  let cursor = 0;
  let effects = [];
  const mockReact = {
    ...require('react'),
    useRef(initial) { return refs[cursor++] ??= { current: initial }; },
    useEffect(effect) { effects.push(effect); },
    useLayoutEffect(effect) { effects.push(effect); },
  };
  const module = { exports: {} };
  new Function('module', 'exports', 'require', compiled)(module, module.exports,
    name => name === 'react' ? mockReact : require(name));
  let top = 0;
  const viewport = {
    scrollHeight: 2000, clientHeight: 400,
    get scrollTop() { return top; },
    set scrollTop(value) { top = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight)); },
  };
  let tree;
  const render = () => {
    cursor = 0;
    effects = [];
    tree = module.exports.ChatArea({ messages: [], clarifyRequest: null, onRespondClarify() {} });
    if (tree.props.ref) tree.props.ref.current = viewport;
    const sentinel = tree.props.children.props.children.at(-1);
    if (sentinel.props.ref) sentinel.props.ref.current = {
      scrollIntoView() { viewport.scrollTop = viewport.scrollHeight; },
    };
    effects.forEach(effect => effect());
  };
  const scroll = position => {
    viewport.scrollTop = position;
    tree.props.onScroll?.({ currentTarget: viewport });
  };
  return { viewport, render, scroll };
}

test('streaming follows the bottom, pauses while reading history and resumes at the bottom', () => {
  const chat = conversation();
  chat.render();
  assert.equal(chat.viewport.scrollTop, 1600);
  chat.viewport.scrollHeight += 200;
  chat.render();
  assert.equal(chat.viewport.scrollTop, 1800);
  chat.scroll(400);
  chat.viewport.scrollHeight += 200;
  chat.render();
  assert.equal(chat.viewport.scrollTop, 400, 'new tokens must preserve the history reading position');
  chat.scroll(chat.viewport.scrollHeight - chat.viewport.clientHeight);
  chat.viewport.scrollHeight += 200;
  chat.render();
  assert.equal(chat.viewport.scrollTop, 2200);
});

test('near-bottom scroll remains following, and a new conversation starts following independently', () => {
  const chat = conversation();
  chat.render();
  chat.scroll(1580);
  chat.viewport.scrollHeight += 100;
  chat.render();
  assert.equal(chat.viewport.scrollTop, 1700);
  chat.scroll(0);
  const next = conversation();
  next.render();
  assert.equal(next.viewport.scrollTop, 1600);
});
