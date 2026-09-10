'use strict';

/**
 * RinCode desktop host RPC method allowlist.
 *
 * Only allowlisted methods can be invoked by the renderer over the Electron IPC bridge.
 * Any unlisted method is rejected immediately before reaching the backend transport.
 */
const RPC_METHOD_ALLOWLIST = Object.freeze(
  new Set([
    // Session operations
    'session.list',
    'session.create',
    'session.close',
    'session.resume',
    'session.delete',
    'session.most_recent',
    'session.title',
    'session.clear',
    'session.undo',
    'session.branch',
    'session.export',

    // Turn operations
    'turn.send',
    'turn.subscribe',
    'turn.unsubscribe',
    'turn.cancel',

    // Attachments & models
    'image.attach',
    'model.options',
    'model.save_key',
    'model.disconnect',
    'model.add_model',
    'model.remove_model',

    // Configuration & setup
    'config.get',
    'config.set',
    'setup.status',

    // System inspection & health
    'system.hello',
    'system.ping',
    'system.version',
    'terminal.resize',

    // Human-in-the-loop responses
    'confirm.respond',
    'clarify.respond',
  ])
);

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
