import React from 'react';
import type { BackendStatus, Project } from '../types';
import { FolderIcon, SidebarToggleIcon } from '../icons';
import { StatusIndicator } from './StatusIndicator';

interface HeaderProps {
  sidebarCollapsed: boolean;
  onToggleSidebar: () => void;
  project: Project | null;
  sessionTitle: string;
  backendStatus: BackendStatus | null;
}

export function Header({
  sidebarCollapsed,
  onToggleSidebar,
  project,
  sessionTitle,
  backendStatus,
}: HeaderProps) {
  return (
    <header className="app-header">
      <div className="header-left">
        <button
          type="button"
          className="sidebar-toggle-btn"
          onClick={onToggleSidebar}
          title={sidebarCollapsed ? '展开侧边栏' : '折叠侧边栏'}
        >
          <SidebarToggleIcon size={18} />
        </button>

        {project && (
          <div className="header-project-badge" title={project.path}>
            <FolderIcon size={12} color="var(--accent)" />
            <span>{project.name}</span>
          </div>
        )}

        <div className="header-session-info">
          <span className="header-session-title">{sessionTitle || '新对话'}</span>
        </div>
      </div>

      <div className="header-right">
        <StatusIndicator status={backendStatus} />
      </div>
    </header>
  );
}
