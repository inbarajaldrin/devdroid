const fs = require('node:fs');
const bareNumber = (value) => String(value || '').split('@')[0].split(':')[0].replace(/\D/g, '');

// Accounts on one machine can share a group. Each records what it sends in sharedFile, so a message one
// engine posted never comes back to another engine as the owner's own words.
function createInboundGuard(ownNumbers = new Set(), sharedFile = null) {
  const daemonSends = new Set();
  const shared = () => { try { return fs.readFileSync(sharedFile, 'utf8').split('\n'); } catch { return []; } };
  return {
    noteDaemonSend(id) {
      if (id) daemonSends.add(id);
      if (daemonSends.size > 600) for (const key of daemonSends) { daemonSends.delete(key); if (daemonSends.size <= 500) break; }
      if (id && sharedFile) {
        fs.appendFileSync(sharedFile, id + '\n', { mode: 0o600 });
        const lines = shared().filter(Boolean);
        if (lines.length > 2000) fs.writeFileSync(sharedFile, lines.slice(-1000).join('\n') + '\n', { mode: 0o600 });
      }
    },
    consumeDaemonSend(id) {
      if (!id) return false;
      if (daemonSends.delete(id)) return true;
      return !!sharedFile && shared().includes(id);
    },
    isOwn(message, chat) {
      if (message?.key?.fromMe) return true;
      const sender = message?.key?.participant || chat || '';
      const number = bareNumber(sender);
      return !!number && ownNumbers.has(number);
    },
  };
}

module.exports = { createInboundGuard, bareNumber };
