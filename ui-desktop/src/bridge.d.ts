export interface Project { id: string; name: string; path: string }
export interface BackendStatus { state: 'starting' | 'ready' | 'stopped' | 'error'; message?: string }
export interface DesktopEvent { projectId: string; method: string; params: any }
export interface DesktopBridge {
  projects(): Promise<Project[]>;
  projectHistory(options?: { query?: string }): Promise<Record<string, { sessions: SessionListItem[]; error?: string }>>;
  removeProject(projectId: string): Promise<{ removed: boolean; projects: Project[] }>;
  setSessionArchived(projectId: string, sessionId: string, archived: boolean): Promise<void>;
  addProject(): Promise<Project | null>;
  connect(projectId: string): Promise<BackendStatus>;
  rpc(projectId: string, method: string, params?: Record<string, unknown>): Promise<any>;
  onEvent(callback: (event: DesktopEvent) => void): () => void;
}
declare global { interface Window { rincode: DesktopBridge } }
import type { SessionListItem } from './types';
