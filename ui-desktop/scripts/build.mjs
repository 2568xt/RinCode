import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const uiDesktopDir = path.resolve(__dirname, '..');
const distDir = path.join(uiDesktopDir, 'dist');
const distElectronDir = path.join(distDir, 'electron');
const distRendererDir = path.join(distDir, 'renderer');

// Ensure target directories exist
fs.mkdirSync(distElectronDir, { recursive: true });
fs.mkdirSync(distRendererDir, { recursive: true });

// 1. Compute and preserve sourceRoot metadata pointing to repository/worktree
const sourceRoot = process.env.RINCODE_SOURCE_ROOT || path.resolve(uiDesktopDir, '..');
const metadata = {
  sourceRoot,
  buildTime: new Date().toISOString(),
  appVersion: '0.1.0',
};

const metadataContent = JSON.stringify(metadata, null, 2);
fs.writeFileSync(path.join(distElectronDir, 'build-metadata.json'), metadataContent, 'utf-8');
fs.writeFileSync(path.join(distDir, 'build-metadata.json'), metadataContent, 'utf-8');
console.log(`[build] Preserved sourceRoot metadata: ${sourceRoot}`);

// 2. Build / copy Electron host files
const electronSrcDir = path.join(uiDesktopDir, 'electron');
const hostFiles = [
  'main.cjs',
  'preload.cjs',
  'backend-manager.cjs',
  'rpc-client.cjs',
  'project-store.cjs',
  'project-history.cjs',
  'session-archive-store.cjs',
  'allowlist.cjs',
  'metadata.cjs',
];

for (const file of hostFiles) {
  const src = path.join(electronSrcDir, file);
  const dest = path.join(distElectronDir, file);
  if (!fs.existsSync(src)) {
    console.error(`[build] Fatal: missing required host file ${src}`);
    process.exit(1);
  }
  fs.copyFileSync(src, dest);
}
console.log(`[build] Host CommonJS modules copied to ${distElectronDir}`);

// 3. Strict verification of renderer entrypoint and esbuild
const rendererEntry = path.join(uiDesktopDir, 'src', 'main.tsx');
const rendererOut = path.join(distRendererDir, 'main.js');

if (!fs.existsSync(rendererEntry)) {
  console.error(`[build] Fatal: missing renderer entrypoint at ${rendererEntry}. No placeholder allowed.`);
  process.exit(1);
}

let esbuild;
try {
  esbuild = await import('esbuild');
} catch (err) {
  console.error(`[build] Fatal: failed to import esbuild: ${err.message}`);
  process.exit(1);
}

// Compile renderer bundle without swallowing errors
try {
  await esbuild.build({
    entryPoints: [rendererEntry],
    outfile: rendererOut,
    bundle: true,
    format: 'esm',
    target: ['es2022', 'chrome120'],
    jsx: 'automatic',
    sourcemap: true,
    define: {
      'process.env.NODE_ENV': '"production"',
    },
  });
  console.log('[build] Compiled React renderer from src/main.tsx');
} catch (err) {
  console.error('[build] Fatal: renderer compilation failed:', err);
  process.exit(1);
}

// 4. Copy and configure index.html with CSS stylesheet link
const indexHtmlSrc = path.join(uiDesktopDir, 'index.html');
const indexHtmlDest = path.join(distDir, 'index.html');

if (!fs.existsSync(indexHtmlSrc)) {
  console.error(`[build] Fatal: missing index.html at ${indexHtmlSrc}`);
  process.exit(1);
}

let htmlContent = fs.readFileSync(indexHtmlSrc, 'utf-8');

// Ensure stylesheet link for generated CSS exists in HTML
const cssOut = path.join(distRendererDir, 'main.css');
const cssLinkTag = '<link rel="stylesheet" href="./renderer/main.css">';
if (!htmlContent.includes(cssLinkTag)) {
  htmlContent = htmlContent.replace('</head>', `  ${cssLinkTag}\n</head>`);
}

fs.writeFileSync(indexHtmlDest, htmlContent, 'utf-8');
console.log('[build] Copied and configured index.html in dist/');

console.log('[build] Build complete.');
