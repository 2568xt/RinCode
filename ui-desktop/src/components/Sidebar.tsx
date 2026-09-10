import React, { useState } from 'react';
import type { Project, SessionListItem } from '../types';
import type { ProjectHistory } from '../hooks/useProjectHistory';
import {
  ChevronDownIcon,
  FolderIcon,
  RefreshCwIcon,
  LogoIcon,
  MessageSquareIcon,
  PlusIcon,
  TrashIcon,
  XIcon,
  SearchIcon,
  ArchiveIcon,
} from '../icons';
import { formatRelativeTime } from '../utils/format';
import './SidebarActions.css';

type SidebarSessionItem = SessionListItem & { archived?: boolean };

interface SidebarProps {
  collapsed: boolean;
  projects: Project[];
  activeProject: Project | null;
  onSelectProject: (project: Project) => void;
  onAddProject: () => void;
  sessions: SessionListItem[];
  activeSessionId: string | null;
  onSelectSession: (project: Project, sessionId: string) => void;
  history: ProjectHistory;
  historyLoading: boolean;
  historyError: string | null;
  onReloadHistory: () => void;
  onNewSession: () => void;
  onDeleteSession: (sessionId: string) => void;
  isTurnRunning: boolean;
  isConfirmPending?: boolean;
  loading?: boolean;
  isSwitchingModel?: boolean;
  onRemoveProject?: (project: Project) => void;
  onArchiveSession?: (project: Project, sessionId: string) => void;
  onOpenSearch?: () => void;
  onOpenArchive?: () => void;
  isManaging?: boolean;
}

export function Sidebar({
  collapsed,
  projects,
  activeProject,
  onSelectProject,
  onAddProject,
  sessions,
  history,
  historyLoading,
  historyError,
  onReloadHistory,
  activeSessionId,
  onSelectSession,
  onNewSession,
  onDeleteSession,
  isTurnRunning,
  isConfirmPending = false,
  loading = false,
  isSwitchingModel = false,
  onRemoveProject,
  onArchiveSession,
  onOpenSearch,
  onOpenArchive,
  isManaging = false,
}: SidebarProps) {
  const [closedProjects, setClosedProjects] = useState<Set<string>>(new Set());
  const isBusy = isTurnRunning || isConfirmPending || loading || isSwitchingModel || isManaging;
  const busyTitle = isTurnRunning
    ? '正在执行任务中，无法进行此操作'
    : isConfirmPending
    ? '请先完成删除确认操作'
    : isSwitchingModel
    ? '正在切换模型中，无法进行此操作'
    : isManaging
    ? '正在更新工作区，请稍候'
    : loading
    ? '正在加载中，请稍候...'
    : '';

  return (
    <aside className={`sidebar ${collapsed ? 'collapsed' : ''}`} inert={collapsed}>
      <div className="sidebar-window-controls" aria-hidden="true" />
      <div className="sidebar-header">
        <div className="sidebar-brand">
          <span className="sidebar-brand-icon"><LogoIcon size={18} /></span>
          <span>RinCode</span>
        </div>

        <div className="sidebar-actions">
          <button
            type="button"
            className="new-chat-btn"
            onClick={onNewSession}
            disabled={isBusy || !activeProject}
            title={isBusy ? busyTitle : '开启新会话'}
          >
            <PlusIcon size={15} />
            <span>新建对话</span>
          </button>
          <div className="sidebar-action-nav">
            <button
              type="button"
              className="sidebar-action-btn"
              onClick={onOpenSearch}
              disabled={isBusy || !onOpenSearch}
              title={isBusy ? busyTitle : '搜索对话'}
            >
              <SearchIcon size={14} />
              <span>搜索对话</span>
            </button>
            <button
              type="button"
              className="sidebar-action-btn"
              onClick={onOpenArchive}
              disabled={isBusy || !onOpenArchive}
              title={isBusy ? busyTitle : '已归档'}
            >
              <ArchiveIcon size={14} />
              <span>已归档</span>
            </button>
          </div>
        </div>

      </div>

      <div className="sidebar-projects">
        <div className="sidebar-section-label workspace-label">
          <span>工作区 · {projects.length}</span>
          <button type="button" className="history-refresh-btn" aria-label="刷新历史对话"
            title="刷新历史对话" disabled={historyLoading || isBusy} onClick={onReloadHistory}>
            <RefreshCwIcon size={12} />
          </button>
        </div>
        {historyError && <div className="project-history-status" role="alert">
          {historyError} <button type="button" onClick={onReloadHistory}>重试</button>
        </div>}
        <nav className="workspace-project-list" aria-label="工作区项目">
          {projects.map(project => {
            const isCurrentProject = project.id === activeProject?.id;
            const expanded = !closedProjects.has(project.id);
            const entry = history[project.id];
            const rawItems = (isCurrentProject ? sessions : entry?.sessions || []) as SidebarSessionItem[];
            const historySessions = (entry?.sessions || []) as SidebarSessionItem[];
            const historyMap = new Map(historySessions.map(s => [s.id, s]));
            const items = rawItems.filter(session => {
              const historyItem = historyMap.get(session.id);
              return !session.archived && !historyItem?.archived;
            });
            const isLoading = isCurrentProject ? loading : historyLoading && !entry;
            return (
              <div className="workspace-project-group" key={project.id} data-project-id={project.id}>
                <div className="workspace-project-row">
                  <button type="button" className="project-expand-btn"
                    aria-label={`${expanded ? '收起' : '展开'} ${project.name} 的对话`}
                    aria-expanded={expanded} onClick={() => setClosedProjects(previous => {
                      const next = new Set(previous);
                      if (expanded) next.add(project.id); else next.delete(project.id);
                      return next;
                    })}>
                    <ChevronDownIcon size={12} className={expanded ? '' : 'is-closed'} />
                  </button>
                  <button type="button" className={`workspace-project ${isCurrentProject ? 'active' : ''}`}
                    data-project-id={project.id} aria-current={isCurrentProject ? 'page' : undefined}
                    disabled={isBusy} title={isBusy ? busyTitle : project.path}
                    onClick={() => {
                      setClosedProjects(previous => { const next = new Set(previous); next.delete(project.id); return next; });
                      if (!isCurrentProject) onSelectProject(project);
                    }}>
                    <FolderIcon size={14} />
                    <span className="project-name">{project.name}</span>
                    <span className="workspace-project-current">{items.length || (isCurrentProject ? '当前' : '')}</span>
                  </button>
                  <button type="button" className="project-remove-btn"
                    aria-label="移除项目" title={isBusy ? busyTitle : '移除项目'}
                    disabled={isBusy || !onRemoveProject}
                    onClick={() => onRemoveProject?.(project)}>
                    <XIcon size={12} />
                  </button>
                </div>
                {expanded && <div className="project-session-list" aria-label={`${project.name} 的对话`}>
                  {entry?.error && <div className="project-history-status" role="alert">{entry.error}</div>}
                  {isLoading ? <div className="project-history-status" role="status">正在加载对话…</div>
                    : items.length === 0 ? <div className="project-history-status">{historySessions.some(session => session.archived) ? '暂无未归档的对话' : '还没有已保存的对话'}</div>
                    : items.map(session => {
                      const selected = isCurrentProject && session.id === activeSessionId;
                      const isSaved = historyMap.has(session.id);
                      return (
                        <div key={session.id} className={`session-item ${selected ? 'active' : ''}`} data-session-id={session.id}>
                          <button type="button" className="session-item-content"
                            onClick={() => onSelectSession(project, session.id)} disabled={isBusy}
                            aria-current={selected ? 'page' : undefined} title={isBusy ? busyTitle : session.title || '新对话'}>
                            <span className="session-item-header">
                              <MessageSquareIcon size={12} />
                              <span className="session-title">{session.title || '新对话'}</span>
                            </span>
                            <span className="session-meta">{formatRelativeTime(session.started_at) || '最近'}{session.message_count === 0 ? ' · 尚无消息' : ''}</span>
                          </button>
                          {isSaved && <button type="button" className="session-archive-btn"
                            onClick={() => onArchiveSession?.(project, session.id)}
                            disabled={isBusy || !onArchiveSession}
                            title={isBusy ? busyTitle : '归档此会话'} aria-label="归档此会话">
                            <ArchiveIcon size={13} />
                          </button>}
                          {isCurrentProject && <button type="button" className="session-delete-btn"
                            onClick={() => onDeleteSession(session.id)} disabled={isBusy}
                            title={isBusy ? busyTitle : '删除此会话'} aria-label="删除此会话">
                            <TrashIcon size={13} />
                          </button>}
                        </div>
                      );
                    })}
                </div>}
              </div>
            );
          })}
        </nav>
      </div>
      <button type="button" className="add-project-btn" onClick={onAddProject} disabled={isBusy}
        title={isBusy ? busyTitle : '选择本地目录作为新项目'}>
        <PlusIcon size={14} /><span>添加本地项目</span>
      </button>

      <div className="sidebar-footer">
        <span>RinCode Desktop</span>
        <span>v0.1.0</span>
      </div>
    </aside>
  );
}
