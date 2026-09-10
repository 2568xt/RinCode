'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { app, BrowserWindow, dialog, ipcMain } = require('electron');

const { BackendManager } = require('./backend-manager.cjs');
const { ProjectStore } = require('./project-store.cjs');
const { readProjectHistory } = require('./project-history.cjs');
const { SessionArchiveStore } = require('./session-archive-store.cjs');

app.setName('RinCode');

/** @type {BrowserWindow | null} */
let mainWindow = null;

/** @type {ProjectStore | null} */
let projectStore = null;

/** @type {BackendManager | null} */
let backendManager = null;
let archiveStore = null;

/**
 * Validates that an incoming IPC invocation originated from the trusted
 * main window and frame. Rejects requests from any unauthorized webContents.
 *
 * @param {Electron.IpcMainInvokeEvent} event
 */
function validateSender(event) {
  if (!mainWindow || mainWindow.isDestroyed()) {
    throw new Error('Unauthorized IPC invocation: main window unavailable');
  }

  if (event.sender.id !== mainWindow.webContents.id) {
    throw new Error('Unauthorized IPC invocation: untrusted sender webContents');
  }

  if (event.senderFrame && event.senderFrame !== mainWindow.webContents.mainFrame) {
    throw new Error('Unauthorized IPC invocation: untrusted frame');
  }
}

/**
 * Creates the primary application window.
 */
function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    title: 'RinCode',
    backgroundColor: '#ffffff',
    titleBarStyle: 'hiddenInset',
    trafficLightPosition: { x: 20, y: 18 },
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, 'preload.cjs'),
    },
  });

  // Security: block external navigations
  mainWindow.webContents.on('will-navigate', (event) => {
    event.preventDefault();
  });

  // Security: block window.open
  mainWindow.webContents.setWindowOpenHandler(() => {
    return { action: 'deny' };
  });

  // Security: deny all renderer permission requests (camera, geolocation, notifications, etc.)
  mainWindow.webContents.session.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false);
  });

  // Resolve HTML entry point:
  // When running from dist/electron/main.cjs -> resolves to dist/index.html
  // When running from electron/main.cjs -> resolves to ui-desktop/index.html
  const primaryHtml = path.resolve(__dirname, '..', 'index.html');
  const fallbackHtml = path.resolve(__dirname, '..', 'dist', 'index.html');

  if (fs.existsSync(primaryHtml)) {
    mainWindow.loadFile(primaryHtml);
  } else if (fs.existsSync(fallbackHtml)) {
    mainWindow.loadFile(fallbackHtml);
  }

  mainWindow.on('closed', async () => {
    mainWindow = null;
    if (backendManager) {
      await backendManager.shutdown();
    }
  });
}

function registerIpcHandlers() {
  // 1. Get persisted projects
  ipcMain.handle('desktop:projects', async (event) => {
    validateSender(event);
    return projectStore.loadProjects();
  });

  ipcMain.handle('desktop:project-history', async (event, options = {}) => {
    validateSender(event);
    const query = options?.query ?? '';
    if (typeof query !== 'string') throw new Error('搜索关键词必须是文本');
    const history = await readProjectHistory(projectStore.loadProjects(), query);
    const archives = archiveStore.load();
    for (const [projectId, entry] of Object.entries(history)) {
      for (const session of entry.sessions) session.archived = archives[projectId]?.[session.id] === true;
    }
    return history;
  });

  ipcMain.handle('desktop:archive-session', async (event, projectId, sessionId, archived) => {
    validateSender(event);
    const project = projectStore.getProject(projectId);
    if (!project || typeof sessionId !== 'string' || typeof archived !== 'boolean') throw new Error('项目或对话无效');
    const history = await readProjectHistory([project]);
    if (!history[projectId]?.sessions.some(session => session.id === sessionId)) throw new Error('请先保存对话，再归档');
    archiveStore.setArchived(projectId, sessionId, archived);
  });

  ipcMain.handle('desktop:remove-project', async (event, projectId) => {
    validateSender(event);
    const project = projectStore.getProject(projectId);
    if (!project) throw new Error('项目不存在');
    const { response } = await dialog.showMessageBox(mainWindow, {
      type: 'question', buttons: ['取消', '移除项目'], defaultId: 0, cancelId: 0,
      message: `从工作区移除「${project.name}」？`,
      detail: '只移除此应用中的项目入口。磁盘上的项目文件和已保存对话都会保留，重新添加目录即可找回。',
    });
    if (response !== 1) return { removed: false, projects: projectStore.loadProjects() };
    const projects = projectStore.removeProject(projectId);
    if (backendManager.activeProject?.id === projectId) await backendManager.shutdown();
    return { removed: true, projects };
  });

  // 2. Add project via native directory chooser
  ipcMain.handle('desktop:add-project', async (event) => {
    validateSender(event);
    const result = await dialog.showOpenDialog(mainWindow, {
      title: '选择项目目录 (Select Project Directory)',
      properties: ['openDirectory'],
    });

    if (result.canceled || !result.filePaths || result.filePaths.length === 0) {
      return null;
    }

    const selectedPath = result.filePaths[0];
    return projectStore.addProject(selectedPath);
  });

  // 3. Connect to single active project runtime
  ipcMain.handle('desktop:connect', async (event, projectId) => {
    validateSender(event);
    if (!projectId || typeof projectId !== 'string') {
      throw new Error('projectId is required');
    }

    const project = projectStore.getProject(projectId);
    if (!project) {
      throw new Error(`Project with ID '${projectId}' not found`);
    }

    return backendManager.connect(project);
  });

  // 4. Dispatch allowlisted RPC request
  ipcMain.handle('desktop:rpc', async (event, projectId, method, params) => {
    validateSender(event);
    return backendManager.rpc(projectId, method, params);
  });
}

// Ensure single Electron instance
const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) {
        mainWindow.restore();
      }
      mainWindow.focus();
    }
  });

  app.whenReady().then(() => {
    const userDataPath = app.getPath('userData');
    projectStore = new ProjectStore(userDataPath);
    archiveStore = new SessionArchiveStore(userDataPath);

    backendManager = new BackendManager({
      onEvent: (event) => {
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send('desktop:event', event);
        }
      },
    });

    registerIpcHandlers();
    createMainWindow();

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        createMainWindow();
      }
    });
  });

  let isQuitting = false;
  app.on('before-quit', async (event) => {
    if (!isQuitting && backendManager) {
      isQuitting = true;
      event.preventDefault();
      try {
        await backendManager.shutdown();
      } finally {
        app.quit();
      }
    }
  });
}
