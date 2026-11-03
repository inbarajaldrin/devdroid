const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
function identity(target, env = process.env) {
  if (target.name) return target.name;
  if (target.kind === 'standing') return target.id;
  if (target.agent === 'claude') {
    const dir = path.join(env.HOME || os.homedir(), '.claude/sessions');
    try {
      for (const file of fs.readdirSync(dir).filter(n => n.endsWith('.json'))) {
        const d = JSON.parse(fs.readFileSync(path.join(dir, file), 'utf8'));
        if (d.sessionId === target.id || env.CLAUDE_CODE_MESSAGING_SOCKET && d.messagingSocketPath === env.CLAUDE_CODE_MESSAGING_SOCKET) return d.name || 'claude';
      }
    } catch { /* No registered readable name; exact return address remains intact. */ }
  } else if (target.id) {
    // Read the managed thread title without modifying its database.
    const db = env.TEXT_CODEX_STATE_DB || path.join(env.HOME || os.homedir(), '.codex/state_5.sqlite');
    if (fs.existsSync(db)) try {
      const script = 'import sqlite3,sys\ndb=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro",uri=True)\nr=db.execute("SELECT COALESCE(name,title) FROM threads WHERE id=?",(sys.argv[2],)).fetchone()\nprint(r[0] if r else "")';
      const title = execFileSync('python3', ['-c', script, db, target.id], { encoding: 'utf8', timeout: 2000 }).replace(/\s+/g, ' ').trim().slice(0, 64);
      if (title) return 'codex · ' + title;
    } catch { /* Exact UUID remains usable without the local title database. */ }
  }
  return target.agent;
}
module.exports = { identity };
