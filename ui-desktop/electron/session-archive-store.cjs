'use strict';

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);

class SessionArchiveStore {
  constructor(userDataDir) {
    this.storePath = path.join(path.resolve(userDataDir), 'session-archives.json');
  }

  load() {
    if (!fs.existsSync(this.storePath)) return {};
    const data = JSON.parse(fs.readFileSync(this.storePath, 'utf8'));
    if (!isObject(data) || !isObject(data.archives) ||
        Object.values(data.archives).some(sessions => !isObject(sessions) ||
          Object.values(sessions).some(value => typeof value !== 'boolean'))) {
      throw new Error('会话归档存储已损坏，未修改原文件');
    }
    return data.archives;
  }

  isArchived(projectId, sessionId) {
    const archives = this.load();
    return Object.hasOwn(archives, projectId) &&
      Object.hasOwn(archives[projectId], sessionId) && archives[projectId][sessionId] === true;
  }

  setArchived(projectId, sessionId, archived) {
    if (typeof projectId !== 'string' || !projectId || typeof sessionId !== 'string' ||
        !sessionId || typeof archived !== 'boolean') {
      throw new TypeError('无效的会话归档参数');
    }
    const archives = this.load();
    const sessions = Object.hasOwn(archives, projectId) ? archives[projectId] : {};
    const updated = { ...archives, [projectId]: { ...sessions, [sessionId]: archived } };
    fs.mkdirSync(path.dirname(this.storePath), { recursive: true });
    const temporary = `${this.storePath}.${crypto.randomUUID()}.tmp`;
    try {
      fs.writeFileSync(temporary, JSON.stringify({ archives: updated }, null, 2), { mode: 0o600 });
      fs.renameSync(temporary, this.storePath);
    } finally {
      if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
    }
    return archived;
  }
}

module.exports = { SessionArchiveStore };
