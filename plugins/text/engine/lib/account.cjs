const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const { insist } = require('./errors.cjs');
function accountDirectory(base, account = 'main') {
  insist(typeof account==='string' && /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,31}$/.test(account), 'INVALID_ACCOUNT', 'Account must be a simple configured name');
  return account === 'main' ? base : path.join(base, 'accounts', account);
}
function runtimeDirectory(base, account = 'main', installedDirectory) {
  const same = (a,b) => {
    const actual = p => { try { return fs.realpathSync(p); } catch { return path.resolve(p); } };
    return actual(a) === actual(b);
  };
  let config;
  try { config = JSON.parse(fs.readFileSync(path.join(installedDirectory, 'config.json'), 'utf8')); } catch (e) { if (!['ENOENT','ENOTDIR'].includes(e.code)) throw e; }
  if (config && (config.account || 'main') === account && (!base || same(base, installedDirectory))) return installedDirectory;
  const root = base && !same(base, installedDirectory) ? base : config?.runtimeRoot || path.join(os.homedir(), '.devdroid/text');
  return accountDirectory(root, account);
}
module.exports = { accountDirectory, runtimeDirectory };
