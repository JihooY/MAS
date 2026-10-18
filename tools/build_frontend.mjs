// Build with the installed Node toolchain; works with npm or pnpm node_modules.
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const cwd = fileURLToPath(new URL('../frontend', import.meta.url));
for (const args of [['node_modules/typescript/bin/tsc','-b'],['node_modules/vite/bin/vite.js','build']]) {
  const result = spawnSync(process.execPath, args, { cwd, stdio: 'inherit', windowsHide: true });
  if (result.status !== 0) process.exit(result.status ?? 1);
}
