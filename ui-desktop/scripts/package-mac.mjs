import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const uiDesktopDir = path.resolve(__dirname, '..');
const outDir = path.join(uiDesktopDir, 'out');

console.log('[package:mac] Step 1: Running build to prepare artifacts and metadata...');
await import('./build.mjs');

console.log('[package:mac] Step 2: Packaging macOS application...');

let packager;
try {
  ({ packager } = await import('@electron/packager'));
} catch {
  console.error('[package:mac] @electron/packager is not installed yet. Run "npm install" first.');
  process.exit(1);
}

const metadataPath = path.join(uiDesktopDir, 'dist', 'build-metadata.json');
const extraResources = [];
if (fs.existsSync(metadataPath)) {
  extraResources.push(metadataPath);
}

try {
  const appPaths = await packager({
    dir: uiDesktopDir,
    name: 'RinCode',
    platform: 'darwin',
    arch: process.arch,
    icon: path.join(uiDesktopDir, 'assets', 'icon.icns'),
    out: outDir,
    overwrite: true,
    prune: true,
    extraResource: extraResources,
    ignore: [
      /^\/src/,
      /^\/electron\/tests/,
      /^\/\.git/,
      /^\/out/,
    ],
  });

  console.log(`[package:mac] Application packaged successfully at: ${appPaths.join(', ')}`);
  console.log('[package:mac] Local sourceRoot runtime metadata embedded.');
} catch (err) {
  console.error('[package:mac] Packaging failed:', err.message);
  process.exit(1);
}
