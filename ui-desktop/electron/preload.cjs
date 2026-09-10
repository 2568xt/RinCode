'use strict';

const { contextBridge, ipcRenderer } = require('electron');

/**
 * RinCode Desktop Secure Preload Bridge.
 *
 * Implements the frozen contract defined in `ui-desktop/src/bridge.d.ts`.
 * Secrets and authentication tokens are kept strictly within the Electron host process.
 * Node integration is disabled; context isolation is enforced.
 */
contextBridge.exposeInMainWorld('rincode', {
  /**
   * Retrieves all persisted projects.
   * @returns {Promise<Array<{ id: string, name: string, path: string }>>}
   */
  projects: () => {
    return ipcRenderer.invoke('desktop:projects');
  },

  /**
   * Displays native directory dialog to add a project.
   * @returns {Promise<{ id: string, name: string, path: string } | null>}
   */
  addProject: () => {
    return ipcRenderer.invoke('desktop:add-project');
  },

  /**
   * Starts or connects to the single active project runtime.
   * @param {string} projectId
   * @returns {Promise<{ state: 'starting' | 'ready' | 'stopped' | 'error', message?: string }>}
   */
  connect: (projectId) => {
    return ipcRenderer.invoke('desktop:connect', projectId);
  },

  /**
   * Dispatches an allowlisted RPC method to the active backend.
   * @param {string} projectId
   * @param {string} method
   * @param {Record<string, unknown>} [params]
   * @returns {Promise<any>}
   */
  rpc: (projectId, method, params) => {
    return ipcRenderer.invoke('desktop:rpc', projectId, method, params);
  },

  /**
   * Subscribes to backend events and status changes.
   * @param {(event: { projectId: string, method: string, params: any }) => void} callback
   * @returns {() => void} Unsubscribe function
   */
  onEvent: (callback) => {
    if (typeof callback !== 'function') {
      throw new TypeError('onEvent callback must be a function');
    }

    const handler = (_event, desktopEvent) => {
      callback(desktopEvent);
    };

    ipcRenderer.on('desktop:event', handler);

    return () => {
      ipcRenderer.removeListener('desktop:event', handler);
    };
  },
});
