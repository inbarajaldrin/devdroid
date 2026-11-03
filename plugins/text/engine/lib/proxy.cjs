const fs = require('node:fs');
const path = require('node:path');
const { remote } = require('./client.cjs');
const { insist, Refusal } = require('./errors.cjs');
function proxy({ host, directory, send = remote }) {
  async function call(payload) {
    const result = await send(host, payload);
    if(!result.ok){const e=new Refusal(result.code || 'PROXY_FAILED',result.error || 'Linked engine refused the request');e.notDelivered=result.notDelivered===true;throw e;} return result;
  }
  return {
    messageId: async () => (await call({ cmd: 'message-id' })).id,
    status: async () => ({ ...(await call({ cmd: 'ping' })), via: host }),
    resolve: async chat => (await call({ cmd: 'resolve', chat })).chat,
    aliases: async chat => (await call({ cmd: 'aliases', chat })).aliases,
    find: async query => (await call({ cmd: 'find', query })).matches,
    createGroup: async subject => (await call({ cmd: 'group-create', subject })).group,
    addToGroup: async (chat, participant) => call({ cmd: 'group-add', chat, participant }),
    members: async chat => call({ cmd: 'members', chat }),
    download: async m => {
      insist(m.forwardedAttachment, 'ATTACHMENT_MISSING', 'Linked machine did not forward the attachment');
      const file = path.join(directory, `attachment-${m.id.replace(/[^a-zA-Z0-9]/g, '')}.${m.media?.extension || 'bin'}`);
      fs.writeFileSync(file, Buffer.from(m.forwardedAttachment, 'base64'), { mode: 0o600 }); return file;
    },
    close() {},
  };
}
module.exports = { proxy };
