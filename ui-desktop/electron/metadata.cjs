'use strict';

const fs = require('node:fs');
const path = require('node:path');

/**
 * Searches upward from `startDir` to locate the source repository root
 * containing both `pyproject.toml` and the `rincode` package directory.
 *
 * @param {string} startDir
 * @returns {string | null}
 */
function findRepoRootUpward(startDir) {
  let current = path.resolve(startDir);
  while (true) {
    const pyproject = path.join(current, 'pyproject.toml');
    const rincodeDir = path.join(current, 'rincode');
    if (fs.existsSync(pyproject) && fs.existsSync(rincodeDir)) {
      return current;
    }
    const parent = path.dirname(current);
    if (parent === current) {
      break;
    }
    current = parent;
  }
  return null;
}

/**
 * Reads embedded build metadata if present.
 * Looks in directory next to this file, or in process.resourcesPath.
 *
 * @returns {{ sourceRoot?: string } | null}
 */
function readBuildMetadata() {
  const candidates = [
    path.join(__dirname, 'build-metadata.json'),
    path.join(__dirname, '..', 'build-metadata.json'),
  ];
  if (typeof process.resourcesPath === 'string') {
    candidates.push(path.join(process.resourcesPath, 'build-metadata.json'));
    candidates.push(path.join(process.resourcesPath, 'app', 'build-metadata.json'));
  }

  for (const candidate of candidates) {
    try {
      if (fs.existsSync(candidate)) {
        const raw = fs.readFileSync(candidate, 'utf-8');
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed.sourceRoot === 'string') {
          return parsed;
        }
      }
    } catch {
      // Ignore read/parse errors for candidate
    }
  }
  return null;
}

/**
 * Resolves the source root path for the RinCode Python runtime.
 *
 * Priority:
 * 1. Explicit `RINCODE_SOURCE_ROOT` environment variable
 * 2. Embedded `build-metadata.json` (created during package:mac / build)
 * 3. Upward directory traversal finding `pyproject.toml` + `rincode/`
 * 4. Fallback relative to repository layout
 *
 * @returns {string}
 */
function getSourceRoot() {
  if (process.env.RINCODE_SOURCE_ROOT) {
    const custom = path.resolve(process.env.RINCODE_SOURCE_ROOT);
    if (fs.existsSync(custom)) {
      return custom;
    }
  }

  const metadata = readBuildMetadata();
  if (metadata && metadata.sourceRoot && fs.existsSync(metadata.sourceRoot)) {
    return metadata.sourceRoot;
  }

  const upward = findRepoRootUpward(__dirname);
  if (upward) {
    return upward;
  }

  // Fallback assuming standard repo layout: <root>/ui-desktop/electron
  return path.resolve(__dirname, '..', '..');
}

/**
 * Resolves the Python executable path per contract:
 * "Host uses explicit RINCODE_PYTHON if set, else source repo .venv/bin/python, else python3."
 *
 * @param {string} [sourceRoot]
 * @returns {string}
 */
function getPythonExecutable(sourceRoot) {
  if (process.env.RINCODE_PYTHON && process.env.RINCODE_PYTHON.trim()) {
    return process.env.RINCODE_PYTHON.trim();
  }

  const root = sourceRoot || getSourceRoot();
  const venvPythonPosix = path.join(root, '.venv', 'bin', 'python');
  if (fs.existsSync(venvPythonPosix)) {
    return venvPythonPosix;
  }

  const venvPythonWin = path.join(root, '.venv', 'Scripts', 'python.exe');
  if (fs.existsSync(venvPythonWin)) {
    return venvPythonWin;
  }

  return 'python3';
}

module.exports = {
  findRepoRootUpward,
  readBuildMetadata,
  getSourceRoot,
  getPythonExecutable,
};
