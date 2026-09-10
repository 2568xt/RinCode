import React, { useState } from 'react';
import type { Project, SessionListItem } from '../types';
import {
  ChevronDownIcon,
  FolderIcon,
  LogoIcon,
  MessageSquareIcon,
  PlusIcon,
  TrashIcon,
} from '../icons';
import { formatRelativeTime } from '../utils/format';

interface SidebarProps {
  collapsed: boolean;
  projects: Project[];
  activeProject: Project | null;
  onSelectProject: (project: Project) => void;
  onAddProject: () => void;
  sessions: SessionListItem[];
  activeSessionId: string | null;
  onSelectSession: (sessionId: string) => void;
  onNewSession: () => void;
  onDeleteSession: (sessionId: string) => void;
  isTurnRunning: boolean;
  isConfirmPending?: boolean;
  loading?: boolean;
}

export function Sidebar({
  collapsed,
  projects,
  activeProject,
  onSelectProject,
  onAddProject,
  sessions,
  activeSessionId,
  onSelectSession,
  onNewSession,
  onDeleteSession,
  isTurnRunning,
  isConfirmPending = false,
  loading = false,
}: SidebarProps) {
  const [showProjectMenu, setShowProjectMenu] = useState(false);

  const isBusy = isTurnRunning || isConfirmPending || loading;
  const busyTitle = isTurnRunning
    ? '正在执行任务中，无法进行此操作'
    : isConfirmPending
    ? '请先完成删除确认操作'
    : loading
    ? '正在加载中，请稍候...'
    : '';

  return (
    <aside className={`sidebar ${collapsed ? 'collapsed' : ''}`}>
      <div className="sidebar-header">
        <div className="sidebar-brand">
          <LogoIcon size={19} color="var(--accent)" />
          <span>RinCode</span>
          <span className="sidebar-brand-badge">Desktop</span>
        </div>

        <div className="project-section">
          {projects.length > 0 ? (
            <div className="project-selector-wrapper">
              <button
                type="button"
                className="project-selector"
                onClick={() => !isBusy && setShowProjectMenu((prev) => !prev)}
                disabled={isBusy}
                title={isBusy ? busyTitle : (activeProject?.path || '')}
                aria-haspopup="listbox"
                aria-expanded={showProjectMenu}
              >
                <div className="project-info">
                  <FolderIcon size={14} color="var(--accent)" />
                  <span className="project-name">
                    {activeProject?.name || '选择项目...'}
                  </span>
                </div>
                <ChevronDownIcon
                  size={13}
                  className={`project-selector-chevron ${showProjectMenu ? 'open' : ''}`}
                />
              </button>

              {showProjectMenu && !isBusy && (
                <div className="project-dropdown-menu">
                  {projects.map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      className={`project-dropdown-item ${p.id === activeProject?.id ? 'active' : ''}`}
                      onClick={() => {
                        onSelectProject(p);
                        setShowProjectMenu(false);
                      }}
                    >
                      <FolderIcon size={13} />
                      <span className="project-dropdown-name">{p.name}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : null}

          <button
            type="button"
            className="add-project-btn"
            onClick={onAddProject}
            disabled={isBusy}
            title={isBusy ? busyTitle : '选择本地目录作为新项目'}
          >
            <PlusIcon size={14} />
            <span>添加本地项目</span>
          </button>
        </div>
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
      </div>

      <div className="sidebar-sessions-list">
        {sessions.length === 0 ? (
          <div
            style={{
              padding: '24px 12px',
              textAlign: 'center',
              fontSize: 12,
              color: 'var(--text-muted)',
            }}
          >
            暂无历史对话
          </div>
        ) : (
          sessions.map((s) => {
            const isActive = s.id === activeSessionId;
            const timeStr = formatRelativeTime(s.started_at);
            return (
              <div
                key={s.id}
                className={`session-item ${isActive ? 'active' : ''}`}
                onClick={() => !isBusy && onSelectSession(s.id)}
                title={isBusy ? busyTitle : (s.title || '新会话')}
                style={{ opacity: isBusy && !isActive ? 0.6 : 1 }}
              >
                <div className="session-item-content">
                  <div className="session-item-header">
                    <MessageSquareIcon size={13} color={isActive ? 'var(--accent)' : 'var(--text-muted)'} />
                    <span className="session-title">{s.title || '新对话'}</span>
                    {s.message_count > 0 && (
                      <span className="session-msg-badge">{s.message_count}</span>
                    )}
                  </div>
                  <div className="session-meta">
                    {timeStr || '最近'}
                    {s.preview ? ` · ${s.preview}` : ''}
                  </div>
                </div>

                <button
                  type="button"
                  className="session-delete-btn"
                  onClick={(e) => {
                    e.stopPropagation();
                    if (!isBusy) {
                      onDeleteSession(s.id);
                    }
                  }}
                  disabled={isBusy}
                  title={isBusy ? busyTitle : '删除此会话'}
                  aria-label="删除此会话"
                >
                  <TrashIcon size={14} />
                </button>
              </div>
            );
          })
        )}
      </div>

      <div className="sidebar-footer">
        <span>RinCode Engine</span>
        <span>v0.1.0</span>
      </div>
    </aside>
  );
}
