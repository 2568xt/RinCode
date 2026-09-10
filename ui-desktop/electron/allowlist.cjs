'use strict';

/**
 * RinCode desktop host RPC method allowlist.
 *
 * Only allowlisted methods can be invoked by the renderer over the Electron IPC bridge.
 * Any unlisted method is rejected immediately before reaching the backend transport.
 */
const RPC_METHOD_ALLOWLIST = new Set([
  'session.list', 'session.create', 'session.resume', 'session.delete', 'session.title',
  'turn.send', 'turn.subscribe', 'turn.unsubscribe', 'turn.cancel',
  'system.hello', 'system.ping', 'system.version',
  'confirm.respond', 'clarify.respond',
  'desktop.model.options', 'desktop.model.select',
]);

/**
 * Checks if a method is allowed to be called by renderer.
 * @param {unknown} method
 * @returns {boolean}
 */
function isMethodAllowed(method) {
  return typeof method === 'string' && RPC_METHOD_ALLOWLIST.has(method);
}

module.exports = {
  RPC_METHOD_ALLOWLIST,
  isMethodAllowed,
};
