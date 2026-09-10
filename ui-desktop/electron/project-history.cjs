'use strict';

const { execFile } = require('node:child_process');
const path = require('node:path');
const { getPythonExecutable, getSourceRoot } = require('./metadata.cjs');

// Projects must come from the host-owned ProjectStore, never renderer paths.
function readProjectHistory(projects, query = '') {
  if (!projects.length) return Promise.resolve({});
  const sourceRoot = getSourceRoot();
  return new Promise((resolve, reject) => {
    const child = execFile(getPythonExecutable(sourceRoot), ['-m', 'rincode.cli.desktop_history'], {
      cwd: sourceRoot,
      env: {
        ...process.env,
        PYTHONPATH: [sourceRoot, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
      },
      timeout: 15000,
      maxBuffer: 4 * 1024 * 1024,
    }, (error, stdout) => {
      if (error) return reject(new Error('无法读取项目历史，请检查本地 Python 运行环境'));
      try {
        resolve(JSON.parse(stdout));
      } catch {
        reject(new Error('项目历史返回了无效数据'));
      }
    });
    child.stdin.on('error', () => {}); // execFile reports process failures above.
    child.stdin.end(JSON.stringify({ projects, query }));
  });
}

module.exports = { readProjectHistory };
