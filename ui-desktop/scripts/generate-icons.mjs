import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

// Optional artwork maintenance command; packaging uses the checked-in assets.
const assets = fileURLToPath(new URL('../assets/', import.meta.url));
const scratch = await fs.mkdtemp(path.join(os.tmpdir(), 'rincode-icons-'));
const iconset = path.join(scratch, 'RinCode.iconset');
await fs.mkdir(iconset);
let browser;
try {
  browser = await chromium.launch({
    headless: true,
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined,
  });
  const page = await browser.newPage({ viewport: { width: 1024, height: 1024 } });
  const svg = await fs.readFile(path.join(assets, 'icon.svg'), 'utf8');
  await page.setContent(`<style>html,body{margin:0;background:transparent}svg{display:block}</style>${svg}`);
  await page.screenshot({ path: path.join(assets, 'icon.png'), omitBackground: true });
  for (const size of [16, 32, 128, 256, 512]) {
    for (const scale of [1, 2]) {
      const pixels = size * scale;
      const output = path.join(iconset, `icon_${size}x${size}${scale === 2 ? '@2x' : ''}.png`);
      execFileSync('sips', ['-z', String(pixels), String(pixels), path.join(assets, 'icon.png'), '--out', output], { stdio: 'pipe' });
    }
  }
  execFileSync('iconutil', ['-c', 'icns', iconset, '-o', path.join(assets, 'icon.icns')]);
  console.log('Generated assets/icon.png and assets/icon.icns from assets/icon.svg');
} finally {
  await browser?.close();
  await fs.rm(scratch, { recursive: true, force: true });
}
