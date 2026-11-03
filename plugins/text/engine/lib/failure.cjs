// A positive non-delivery signal is required; error prose is never evidence.
function transient(error) {
  return error?.notDelivered === true && error.code === 'WA_RECONNECTING';
}
module.exports = { transient };
