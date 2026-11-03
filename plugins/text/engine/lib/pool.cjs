const { insist } = require('./errors.cjs');
class Pool {
  constructor(limit = 4, capacity = 100) { this.limit = limit; this.capacity = capacity; this.active = 0; this.pending = []; }
  run(work) {
    insist(this.pending.length < this.capacity, 'DELIVERY_BUSY', 'Delivery queue is full; input remains pending');
    return new Promise((resolve, reject) => { this.pending.push({ work, resolve, reject }); this.drain(); });
  }
  drain() {
    while (this.active < this.limit && this.pending.length) {
      const item = this.pending.shift(); this.active++;
      Promise.resolve().then(item.work).then(item.resolve, item.reject).finally(() => { this.active--; this.drain(); });
    }
  }
}
module.exports = { Pool };
