class Refusal extends Error {
  constructor(code, message) { super(message); this.code = code; }
}
function insist(condition, code, message) {
  if (!condition) throw new Refusal(code, message);
}
module.exports = { Refusal, insist };
