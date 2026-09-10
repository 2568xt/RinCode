import React from 'react';
import type { Project } from '../types';
import { CodeIcon, FolderIcon, LogoIcon, CheckIcon, MessageSquareIcon } from '../icons';

interface EmptyStateProps {
  project: Project | null;
  onAddProject: () => void;
}

export function EmptyState({ project, onAddProject }: EmptyStateProps) {
  return (
    <div className="empty-state-card">
      <div className="empty-state-icon" aria-hidden="true"><LogoIcon size={28} /></div>
      <h1 className="empty-state-title">今天，想做点什么？</h1>
      <p className="empty-state-desc">
        {project ? '读懂项目，打磨代码，让想法更进一步。' : '打开一个项目，从你的下一个想法开始。'}
      </p>
      {!project && (
        <button type="button" className="open-project-btn" onClick={onAddProject}>
          <FolderIcon size={16} />打开项目
        </button>
      )}
    </div>
  );
}

interface QuickPromptsProps {
  onSelectPrompt: (prompt: string) => void;
  disabled?: boolean;
}

export function QuickPrompts({ onSelectPrompt, disabled }: QuickPromptsProps) {
  const prompts = [
    { title: '了解项目', Icon: FolderIcon, prompt: '请分析当前工作区的核心目录结构与主要模块的职责。' },
    { title: '审查改动', Icon: CodeIcon, prompt: '请查看当前项目的 git status 和近期的修改，并给出总结。' },
    { title: '查找待办', Icon: CheckIcon, prompt: '请检索代码中所有的 TODO 和 FIXME 标记，并列出位置与内容。' },
    { title: '规划功能', Icon: MessageSquareIcon, prompt: '我想在当前项目中添加一个新特性，请先帮我梳理实现步骤。' },
  ];

  return (
    <div className="quick-prompts-grid" aria-label="快捷任务">
      {prompts.map(({ title, Icon, prompt }) => (
        <button key={title} type="button" className="quick-prompt-card" disabled={disabled}
          onClick={() => onSelectPrompt(prompt)} title={prompt}>
          <Icon size={15} /><span>{title}</span>
        </button>
      ))}
    </div>
  );
}
