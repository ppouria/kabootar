import { copyFileSync, existsSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = path.resolve(__dirname, '..');
const sourceDir = path.join(rootDir, 'node_modules', 'lucide-static', 'icons');
const outputDir = path.join(rootDir, 'static', 'vendor', 'lucide');
const icons = ['search', 'refresh-cw', 'settings-2', 'menu', 'arrow-left', 'bug', 'languages'];

mkdirSync(outputDir, { recursive: true });

for (const icon of icons) {
  const source = path.join(sourceDir, `${icon}.svg`);
  const target = path.join(outputDir, `${icon}.svg`);
  if (!existsSync(source)) {
    throw new Error(`Missing Lucide icon: ${source}`);
  }
  copyFileSync(source, target);
  console.log(`[icon-sync] ${icon}.svg`);
}
