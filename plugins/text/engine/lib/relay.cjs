const { generateKeyPairSync, sign, verify, randomUUID } = require('node:crypto');
const { insist } = require('./errors.cjs');
// The signing key lives only in this engine process. The public socket cannot mint
// WhatsApp input or publication authority; relays verify a peer's key over SSH.
class Relay {
  #private;
  constructor({ host, peers = [], keyFor, now = Date.now }) {
    const keys = generateKeyPairSync('ed25519'); this.#private = keys.privateKey;
    this.publicKey = keys.publicKey.export({ type: 'spki', format: 'pem' });
    Object.assign(this, { host, peers, keyFor, now }); this.seen = new Map();
  }
  seal(to, kind, data) {
    const payload = { from: this.host, to, kind, data, id: randomUUID(), at: this.now() };
    return { payload, signature: sign(null, Buffer.from(JSON.stringify(payload)), this.#private).toString('base64') };
  }
  async open(envelope) {
    const p = envelope?.payload;
    insist(p && p.to === this.host && this.peers.includes(p.from) && Math.abs(this.now() - p.at) < 120000, 'UNTRUSTED_RELAY', 'Unknown, expired or misaddressed relay');
    const key = await this.keyFor(p.from);
    insist(verify(null, Buffer.from(JSON.stringify(p)), key, Buffer.from(envelope.signature || '', 'base64')), 'UNTRUSTED_RELAY', 'Relay signature does not match its engine');
    insist(!this.seen.has(p.id), 'RELAY_REPLAY', 'Relay already handled');
    this.seen.set(p.id, p.at);
    for (const [id, at] of this.seen) if (this.now() - at > 120000 || this.seen.size > 1000) this.seen.delete(id);
    return p;
  }
}
module.exports = { Relay };
