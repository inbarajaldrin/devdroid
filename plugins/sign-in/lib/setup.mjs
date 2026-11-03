import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { ROOT, HOME, insist, Refusal, run, json, read, privateDir, writeNew, locked,atomicJSON } from './core.mjs';
import { load,validate } from './accounts.mjs';
import { pins, platform, binary } from '../browser/settings.mjs';
import { daemonStatus, sharedUsers } from './daemon.mjs';
export const digest = (bytes, algorithm = 'sha256', encoding = 'hex') => crypto.createHash(algorithm).update(bytes).digest(encoding);
export function checksum(bytes, expected, algorithm = 'sha256', encoding = 'hex') {
  insist(digest(bytes, algorithm, encoding) === expected, 'CHECKSUM_MISMATCH', `${algorithm} checksum mismatch`);
}
export function fileHashes(root, relative = '') {
  const result = {};
  for (const name of fs.readdirSync(path.join(root, relative))) {
    const key = path.join(relative, name), file = path.join(root, key);
    const s = fs.lstatSync(file);
    if (s.isSymbolicLink()) result[key] = `link:${fs.readlinkSync(file)}`;
    else if (s.isDirectory()) Object.assign(result, fileHashes(root, key));
    else if (s.isFile()) result[key] = digest(fs.readFileSync(file));
  }
  return result;
}
export function verifyFiles(root, expected) {
  const actual = fileHashes(root);
  insist(JSON.stringify(Object.keys(actual).sort()) === JSON.stringify(Object.keys(expected).sort()), 'INSTALL_CHANGED', `Installed file list changed: ${root}`);
  for (const [file, hash] of Object.entries(expected)) insist(actual[file] === hash, 'INSTALL_CHANGED', `Installed bytes changed: ${path.join(root, file)}`);
}
export function verifyRuntime(root = ROOT) {
  const expected = read(path.join(root, 'browser/runtime.sha256.json'));
  for (const [file, hash] of Object.entries(expected)) {
    insist(!path.isAbsolute(file) && !file.split('/').includes('..'), 'CHECKSUM_MANIFEST', 'Runtime paths must be relative');
    checksum(fs.readFileSync(path.join(root, file)), hash);
  }
  return expected;
}
const components = () => ['opencli', `chrome/${pins.chrome.version}`, `opencli-extension/${pins.extension.version}`, 'runtime'];
export async function download(url, dest, hash, algorithm = 'sha256', encoding = 'hex', fetcher = fetch) {
  const response = await fetcher(url, { signal: AbortSignal.timeout(300000) });
  insist(response.ok, 'DOWNLOAD_FAILED', `Download failed (${response.status}): ${url}`);
  const bytes = Buffer.from(await response.arrayBuffer());
  checksum(bytes, hash, algorithm, encoding);
  fs.writeFileSync(dest, bytes, { mode: 0o600, flag: 'wx' });
}
async function extract(archive, dest) {
  privateDir(dest);
  if (process.platform === 'darwin') await run('ditto', ['-x', '-k', archive, dest]);
  else await run('unzip', ['-q', archive, '-d', dest]);
}
function dependencyLock() {
  const lock = read(path.join(ROOT, 'browser/package-lock.json'));
  const cli = lock.packages['node_modules/@jackwener/opencli'];
  insist(cli?.version === pins.opencli.version && cli?.integrity === pins.opencli.integrity, 'LOCK_MISMATCH', 'OpenCLI package lock does not match its pin');
  for (const [name, entry] of Object.entries(lock.packages)) if (name) insist(entry.integrity && entry.resolved?.startsWith('https://registry.npmjs.org/'), 'LOCK_MISMATCH', 'Every npm dependency must have registry URL and integrity');
}
export async function setup(accountsFile, accountsFrom, dependencies = {}) {
  const d = { execute: run, download, extract, root: HOME, target: platform(), ...dependencies };
  insist(Number(process.versions.node.split('.')[0]) >= 22, 'NODE_VERSION', 'Node 22 or newer is required');
  verifyRuntime(); dependencyLock();
  // Validate every supplied input before staging or installing anything.
  const imported = accountsFrom ? validate(read(path.resolve(accountsFrom))) : null;
  insist(!imported||!Object.keys(imported.rulings||{}).length&&Object.values(imported.accounts).every(a=>!a.google&&!a.permissions.length),'SETUP_AUTHORIZATION_IMPORT','Setup cannot import authorization, grants, Production attestations or rulings; use human steps');
  const before=fs.existsSync(accountsFile)?read(accountsFile):null;
  const interviewOnly=before&&Object.keys(before.accounts||{}).length===0&&Object.keys(before.rulings||{}).length>0;
  const destinationReady=()=>!fs.existsSync(accountsFile)||interviewOnly&&JSON.stringify(read(accountsFile))===JSON.stringify(before);
  insist(!imported || destinationReady(), 'ACCOUNTS_EXISTS', 'Accounts file exists; setup never overwrites accounts');
  if (fs.existsSync(accountsFile)) load(accountsFile);
  const receiptFile = path.join(d.root, 'install.json');
  if (fs.existsSync(receiptFile)) {
    insist(!imported, 'ALREADY_INSTALLED', 'Use create for a new account on an installed browser layer');
    const upgraded = await upgradeRuntime(d, receiptFile);
    const report = await doctor(accountsFile, { ...d, connectivity: false });
    return upgraded ? { ...report, runtimeUpgraded: upgraded } : report;
  }
  return locked(path.join(d.root, 'setup'), async () => {
    const relative = components();
    const extensionComponent=`opencli-extension/${pins.extension.version}`;
    for (const item of relative) insist(item===extensionComponent||!fs.existsSync(path.join(d.root, item)), 'INSTALL_EXISTS', `Refusing to replace ${path.join(d.root, item)}; inspect with doctor`);
    const stage = fs.mkdtempSync(path.join(d.root, '.setup-'));
    fs.chmodSync(stage, 0o700);
    try {
      const cliDir = path.join(stage, 'opencli'); privateDir(cliDir);
      for (const name of ['package.json', 'package-lock.json']) fs.copyFileSync(path.join(ROOT, 'browser', name), path.join(cliDir, name));
      await d.execute('npm', ['ci', '--prefix', cliDir, '--ignore-scripts', '--no-audit', '--no-fund'], { timeout: 300000 });
      const zip = path.join(stage, 'chrome.zip');
      await d.download(`https://storage.googleapis.com/chrome-for-testing-public/${pins.chrome.version}/${d.target}/chrome-${d.target}.zip`, zip, pins.chrome.md5[d.target], 'md5', 'base64');
      await d.extract(zip, path.join(stage, 'chrome', pins.chrome.version)); fs.unlinkSync(zip);
      const extZip = path.join(stage, 'extension.zip'), extDir = path.join(stage, 'opencli-extension', pins.extension.version);
      await d.download(pins.extension.url, extZip, pins.extension.sha256);
      await d.extract(extZip, extDir); fs.unlinkSync(extZip);
      insist(read(path.join(extDir, 'manifest.json')).version === pins.extension.version, 'EXTENSION_VERSION', 'Wrong extension manifest version');
      const existingExtension=path.join(d.root,extensionComponent);
      if(fs.existsSync(existingExtension))verifyFiles(existingExtension,fileHashes(extDir));
      const version = (await d.execute(path.join(cliDir, 'node_modules/.bin/opencli'), ['--version'])).stdout.trim();
      insist(version === pins.opencli.version, 'OPENCLI_VERSION', 'Wrong installed OpenCLI version');
      const chromeVersion = (await d.execute(binary(stage, d.target), ['--version'])).stdout.trim();
      insist(chromeVersion.includes(pins.chrome.version), 'CHROME_VERSION', 'Wrong Chrome for Testing version');
      const runtime = path.join(stage, 'runtime'); privateDir(runtime);
      for (const dir of ['lib', 'scripts', 'browser', 'skills', '.claude-plugin']) fs.cpSync(path.join(ROOT, dir), path.join(runtime, dir), { recursive: true });
      for (const name of ['README.md', 'accounts.example.json','LICENSE','NOTICE']) fs.copyFileSync(path.join(ROOT, name), path.join(runtime, name));
      verifyRuntime(runtime);
      const receipt = { version: 1, platform: d.target, pins, files: {} };
      for (const item of relative) receipt.files[item] = fileHashes(path.join(stage, item));
      const publish = () => {
        // Recheck after downloads; an account created in the meantime must never
        // be overwritten or strand an otherwise successful installation.
        insist(!imported || destinationReady(), 'ACCOUNTS_EXISTS', 'Accounts file appeared or changed during setup; no component was published');
        for (const item of relative) {
          if(item===extensionComponent&&fs.existsSync(path.join(d.root,item)))verifyFiles(path.join(d.root,item),receipt.files[item]);
          else insist(!fs.existsSync(path.join(d.root, item)), 'INSTALL_EXISTS', `Component appeared during setup: ${item}`);
        }
        // Publish only completed, validated components. Existing profiles and
        // account definitions are never modified, copied or opened by setup.
        for (const item of relative) {
          if(item===extensionComponent&&fs.existsSync(path.join(d.root,item)))continue;
          privateDir(path.dirname(path.join(d.root, item)));
          fs.renameSync(path.join(stage, item), path.join(d.root, item));
        }
        if (imported){if(interviewOnly)atomicJSON(accountsFile,{...imported,rulings:before.rulings});else writeNew(accountsFile, imported);}
        writeNew(receiptFile, receipt);
      };
      if (imported) await locked(accountsFile, publish); else publish();
      return { installed: true, root: d.root, accountsFile, accountsImported: !!imported, bridgesStarted: false, pins };
    } finally { fs.rmSync(stage, { recursive: true, force: true }); }
  });
}
// A newer plugin replaces only the installed runtime folder, after verifying it; the pinned browser, OpenCLI,
// extension, accounts and profiles are never touched. The previous runtime is kept beside it.
async function upgradeRuntime(d, receiptFile) {
  const receipt = read(receiptFile);
  insist(receipt.version === 1 && JSON.stringify(receipt.pins) === JSON.stringify(pins), 'INSTALL_PIN_MISMATCH', 'Installation pins differ; no upgrade was attempted');
  const installed = path.join(d.root, 'runtime');
  const source = JSON.stringify(read(path.join(ROOT, 'browser/runtime.sha256.json')));
  let current = null;
  try { current = JSON.stringify(read(path.join(installed, 'browser/runtime.sha256.json'))); } catch { /* absent runtime is repaired by doctor's report */ }
  if (current === source) return null;
  return locked(path.join(d.root, 'setup'), async () => {
    const stage = fs.mkdtempSync(path.join(d.root, '.upgrade-'));
    fs.chmodSync(stage, 0o700);
    try {
      const runtime = path.join(stage, 'runtime'); privateDir(runtime);
      for (const dir of ['lib', 'scripts', 'browser', 'skills', '.claude-plugin']) fs.cpSync(path.join(ROOT, dir), path.join(runtime, dir), { recursive: true });
      for (const name of ['README.md', 'accounts.example.json','LICENSE','NOTICE']) fs.copyFileSync(path.join(ROOT, name), path.join(runtime, name));
      verifyRuntime(runtime);
      const backup = path.join(d.root, 'runtime-backup-' + Date.now());
      if (fs.existsSync(installed)) fs.renameSync(installed, backup);
      fs.renameSync(runtime, installed);
      atomicJSON(receiptFile, { ...receipt, files: { ...receipt.files, runtime: fileHashes(installed) } });
      return { from: current ? 'older runtime' : 'missing runtime', backup: fs.existsSync(backup) ? backup : null };
    } finally { fs.rmSync(stage, { recursive: true, force: true }); }
  });
}
export async function doctor(accountsFile, dependencies = {}) {
  const d = { root: HOME, execute: run, connectivity: true, status:daemonStatus, shared:sharedUsers, ...dependencies }, checks = [];
  const examine = async (name, work) => {
    try { checks.push({ name, ok: true, detail: await work() }); return true; }
    catch (e) { checks.push({ name, ok: false, code: e.code || 'CHECK_FAILED', error: e.message }); return false; }
  };
  const sourceOK = await examine('runtime-source', () => { verifyRuntime(); dependencyLock(); return 'checksums and dependency integrity match'; });
  await examine('accounts', () => ({ accounts: Object.keys(load(accountsFile).accounts) }));
  await examine('optional-gws', async () => (await import('./google.mjs')).doctorGoogle(d.root));
  await examine('private-holder',async()=>{const others=await d.shared();insist(!others.length,'SHARED_HOST','Other human users can reach localhost DevTools; do not hold accounts here');return 'No other local human accounts found; private-machine ownership must also be confirmed';});
  const installedOK = await examine('installed-files', () => {
    const receipt = read(path.join(d.root, 'install.json'));
    insist(receipt.version === 1 && JSON.stringify(receipt.pins) === JSON.stringify(pins), 'INSTALL_PIN_MISMATCH', 'Installation pins differ; no repair was attempted');
    insist(receipt.files && JSON.stringify(Object.keys(receipt.files).sort()) === JSON.stringify(components().sort()) && components().every(item => Object.keys(receipt.files[item]).length > 0), 'INSTALL_RECEIPT', 'Installation receipt must cover all four components');
    for (const [item, hashes] of Object.entries(receipt.files)) verifyFiles(path.join(d.root, item), hashes);
    for (const [file, hash] of Object.entries(verifyRuntime())) checksum(fs.readFileSync(path.join(d.root, 'runtime', file)), hash);
    return { platform: receipt.platform, components: Object.keys(receipt.files) };
  });
  if (!sourceOK || !installedOK) {
    for (const name of ['opencli-version', 'extension-version', 'chrome-version', ...(d.connectivity ? ['daemon-status'] : [])]) checks.push({ name, ok: false, skipped: true, reason: 'integrity-not-proven' });
    throw new Refusal('DOCTOR_FAILED', 'Installed integrity is not proven; no binary was executed', { healthy: false, checks, repaired: false });
  }
  const cli = path.join(d.root, 'opencli/node_modules/.bin/opencli');
  await examine('opencli-version', async () => {
    const version = (await d.execute(cli, ['--version'])).stdout.trim();
    insist(version === pins.opencli.version, 'OPENCLI_VERSION', `Found ${version}`); return version;
  });
  await examine('extension-version', () => {
    const version = read(path.join(d.root, 'opencli-extension', pins.extension.version, 'manifest.json')).version;
    insist(version === pins.extension.version, 'EXTENSION_VERSION', `Found ${version}`); return version;
  });
  await examine('chrome-version', async () => {
    const version = (await d.execute(binary(d.root), ['--version'])).stdout.trim();
    insist(version.includes(pins.chrome.version), 'CHROME_VERSION', `Found ${version}`); return version;
  });
  if (d.connectivity) await examine('daemon-status', async () => {
    const report=await d.status();
    insist(report?.daemonVersion===pins.opencli.version,'DAEMON_CONFLICT','Matching daemon is not running; doctor does not start or replace it');
    return {daemonVersion:report.daemonVersion,profiles:report.profiles?.length||0,probe:'status-only',spawned:false};
  });
  if(d.connectivity)await examine('bridge-work',()=>d.bridgeStatus?d.bridgeStatus(accountsFile):(import('../browser/ext-bridge.mjs').then(layer=>layer.command(accountsFile,'state'))));
  const report = { healthy: checks.every(c => c.ok), checks, repaired: false };
  insist(report.healthy, 'DOCTOR_FAILED', 'Browser layer needs attention; no repair was attempted', report);
  return report;
}
