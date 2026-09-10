import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const compiled = buildSync({
  stdin: {
    contents: "export { MarkdownView } from './src/components/MarkdownView'; export { parseTable } from './src/utils/markdownTable';",
    resolveDir: fileURLToPath(new URL('../..', import.meta.url)),
  },
  bundle: true, format: 'cjs', platform: 'node', packages: 'external', write: false,
  loader: { '.css': 'empty' },
}).outputFiles[0].text;
const module = { exports: {} };
new Function('module', 'exports', 'require', compiled)(module, module.exports, createRequire(import.meta.url));
const { MarkdownView, parseTable } = module.exports;
const parse = (text) => parseTable(text.split('\n'), 0);
const render = (content) => renderToStaticMarkup(React.createElement(MarkdownView, { content }));
const sample = '| 问题 | 方案 |\n| --- | --- |\n| README 主线不清晰 | 按使用流程重新组织 |';

test('renders the reported pipe table as semantic header and data cells', () => {
  const html = render(sample);
  assert.match(html, /<table class="markdown-table"><thead>/);
  assert.match(html, /<th scope="col">问题<\/th>/);
  assert.match(html, /<td>按使用流程重新组织<\/td>/);
  assert.doesNotMatch(html, /\| --- \|/);
});

test('accepts optional edge pipes, GFM alignment, and normalizes body column counts', () => {
  const table = parse('左 | 中 | 右\n:- | :-: | -:\n a | b \n c | d | e | ignored');
  assert.deepEqual(table.alignments, ['left', 'center', 'right']);
  assert.deepEqual(table.rows, [['a', 'b', ''], ['c', 'd', 'e']]);
  assert.match(render('左 | 右\n:--- | ---:\nA | B'), /<td style="text-align:right">B<\/td>/);
  assert.deepEqual(parse('| title |\n| --- |').rows, []);
  assert.deepEqual(parse('title\n| --- |').headers, ['title']);
});

test('requires a complete matching delimiter and does not turn ordinary pipes into tables', () => {
  for (const text of ['A | B\n---', 'A | B\n--- |', 'A | B\n--- | text', 'A \\| B\n---', '# A | B\n--- | ---', '    A | B\n    --- | ---']) {
    assert.equal(parse(text), null, text);
  }
  assert.doesNotMatch(render('Run a | b\nSome explanation'), /<table/);
});

test('preserves escaped pipes in text, inline code, emphasis, and trailing cells', () => {
  const text = ['| f\\|oo | command |', '| --- | --- |', '| x | `a\\|b` |',
    '| y | **a\\|b** |', 'left | ends\\|'].join('\n');
  const table = parse(text);
  assert.deepEqual(table.headers, ['f|oo', 'command']);
  assert.deepEqual(table.rows.at(-1), ['left', 'ends|']);
  const html = render(text);
  assert.match(html, /<code class="inline-code">a\|b<\/code>/);
  assert.match(html, /<strong>a\|b<\/strong>/);
  assert.deepEqual(parse(String.raw`a | b | c
- | - | -
one\\|two|three`).rows, [[String.raw`one\\`, 'two', 'three']]);
});

test('fenced code, including unclosed or longer fences, never creates a table', () => {
  for (const fence of ['```', '~~~~']) {
    const html = render(`${fence}markdown\n${sample}\n${fence}`);
    assert.match(html, /<pre class="code-block-content">/);
    assert.doesNotMatch(html, /<table/);
    assert.doesNotMatch(render(`${fence}\n${sample}`), /<table/);
  }
  assert.doesNotMatch(render('````\n```\n' + sample + '\n````'), /<table/);
});

test('table rows stop at blank lines and other block starts', () => {
  for (const block of ['# Next', '> Quote', '- List', '1. List', '---', '```js']) {
    const table = parse(sample + '\n' + block);
    assert.equal(table.rows.length, 1, block);
    assert.equal(table.nextLine, 3, block);
  }
  assert.match(render(sample + '\n\n# Next'), /<\/table><\/div><h1>Next<\/h1>/);
  assert.deepEqual(parse('a | b\n- | -\nfirst\n\nnext').rows, [['first', '']]);
});

test('table cells share inline formatting and render safe links without raw HTML', () => {
  const html = render('| 文档 | 操作 |\n| --- | --- |\n| `README.md` | **重点** *说明* [文档](https://example.com/a_(b)) |');
  assert.match(html, /<strong>重点<\/strong> <em>说明<\/em>/);
  assert.match(html, /<a href="https:\/\/example.com\/a_\(b\)" target="_blank" rel="noreferrer noopener">文档<\/a>/);
  const unsafe = render('| name |\n| --- |\n| [x](javascript:alert(1)) <script>alert(1)</script> |');
  assert.doesNotMatch(unsafe, /href=|<script>/);
  assert.match(unsafe, /&lt;script&gt;/);
});

test('partial streaming input never throws and keeps recognized table rows rectangular', () => {
  const text = sample + '\n| 新问题 | `a\\|b` |';
  for (let end = 0; end <= text.length; end++) {
    const prefix = text.slice(0, end);
    assert.doesNotThrow(() => render(prefix));
    const table = parse(prefix);
    if (table) for (const row of table.rows) assert.equal(row.length, table.headers.length);
  }
  assert.equal(parse(text).rows.length, 2);
});
