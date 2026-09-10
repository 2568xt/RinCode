'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const PROJECTS_FILENAME = 'projects.json';

/**
 * Computes a stable project ID for a given filesystem path.
 *
 * @param {string} projectPath
 * @returns {string} 16-character hexadecimal hash
 */
function makeProjectId(projectPath) {
  const canonical = path.resolve(projectPath).normalize();
  return crypto.createHash('sha256').update(canonical).digest('hex').slice(0, 16);
}

/**
 * Derives a human-readable display name from a directory path.
 *
 * @param {string} projectPath
 * @returns {string}
 */
function makeProjectName(projectPath) {
  const canonical = path.resolve(projectPath).normalize();
  const base = path.basename(canonical);
  return base && base.length > 0 ? base : 'Project';
}

class ProjectStore {
  /**
   * @param {string} userDataDir Directory path for Electron userData
   */
  constructor(userDataDir) {
    this.userDataDir = path.resolve(userDataDir);
    this.storePath = path.join(this.userDataDir, PROJECTS_FILENAME);
  }

  /**
   * Loads all persisted projects.
   *
   * @returns {Array<{ id: string, name: string, path: string }>}
   */
  loadProjects() {
    if (!fs.existsSync(this.storePath)) {
      return [];
    }
    const data = JSON.parse(fs.readFileSync(this.storePath, 'utf-8'));
    const projects = Array.isArray(data) ? data : data?.projects;
    if (!Array.isArray(projects) || projects.some(p =>
      !p || typeof p.id !== 'string' || typeof p.name !== 'string' || typeof p.path !== 'string')) {
      throw new Error('项目列表存储已损坏，未修改原文件');
    }
    return projects;
  }

  /**
   * Saves projects array to persistent storage atomically.
   *
   * @param {Array<{ id: string, name: string, path: string }>} projects
   */
  saveProjects(projects) {
    if (!fs.existsSync(this.userDataDir)) {
      fs.mkdirSync(this.userDataDir, { recursive: true });
    }
    const data = JSON.stringify({ projects }, null, 2);
    const tmpPath = `${this.storePath}.${Date.now()}.${Math.random().toString(36).slice(2, 8)}.tmp`;
    fs.writeFileSync(tmpPath, data, 'utf-8');
    fs.renameSync(tmpPath, this.storePath);
  }

  /**
   * Adds or retrieves a project for the specified directory path.
   * If the directory is already persisted, returns the existing record.
   *
   * @param {string} dirPath
   * @returns {{ id: string, name: string, path: string }}
   */
  addProject(dirPath) {
    const resolvedPath = path.resolve(dirPath).normalize();
    const id = makeProjectId(resolvedPath);
    const projects = this.loadProjects();

    const existing = projects.find(p => p.id === id || path.resolve(p.path).normalize() === resolvedPath);
    if (existing) {
      return existing;
    }

    const newProject = {
      id,
      name: makeProjectName(resolvedPath),
      path: resolvedPath,
    };

    projects.push(newProject);
    this.saveProjects(projects);
    return newProject;
  }

  /**
   * Retrieves a project by its stable ID.
   *
   * @param {string} projectId
   * @returns {{ id: string, name: string, path: string } | null}
   */
  getProject(projectId) {
    const projects = this.loadProjects();
    return projects.find(p => p.id === projectId) || null;
  }

  /** Remove only the app registration; project files and history remain intact. */
  removeProject(projectId) {
    const projects = this.loadProjects();
    const remaining = projects.filter(project => project.id !== projectId);
    if (remaining.length !== projects.length) this.saveProjects(remaining);
    return remaining;
  }
}

module.exports = {
  PROJECTS_FILENAME,
  makeProjectId,
  makeProjectName,
  ProjectStore,
};
