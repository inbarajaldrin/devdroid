import path from 'node:path';
import { ROOT, HOME, read, insist } from '../lib/core.mjs';

export const pins = read(path.join(ROOT, 'browser/versions.json'));
export function platform(os = process.platform, arch = process.arch) {
  const value = os === 'darwin' && ['arm64', 'x64'].includes(arch) ? `mac-${arch}` : os === 'linux' && arch === 'x64' ? 'linux64' : null;
  insist(value && pins.chrome.md5[value], 'PLATFORM_UNSUPPORTED', 'Chrome for Testing setup supports macOS arm64/x64 and Linux x64'); return value;
}
export function binary(root = HOME, target = platform()) {
  return path.join(root, 'chrome', pins.chrome.version, `chrome-${target}`, target.startsWith('mac-') ? 'Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing' : 'chrome');
}
