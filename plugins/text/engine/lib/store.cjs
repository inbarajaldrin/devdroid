const fs = require('node:fs');
const path = require('node:path');
function read(file, initial) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8'),(_key,value)=>value?.type==='Buffer' && Array.isArray(value.data)?Buffer.from(value.data):value); }
  catch (e) { if (e.code === 'ENOENT') return structuredClone(initial); if(e instanceof SyntaxError)e.code='INVALID_JSON';e.message=String(file)+': '+e.message;e.file=file;throw e; }
}
function write(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  const temp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(value, null, 2) + '\n', { mode: 0o600 });
  fs.renameSync(temp, file);
}
module.exports = { read, write };
