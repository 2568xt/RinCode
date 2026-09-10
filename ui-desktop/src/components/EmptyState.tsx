import React from 'react';
import type { Project } from '../types';
import { SparklesIcon } from '../icons';

interface EmptyStateProps {
  project: Project | null;
  onSelectPrompt: (prompt: string) => void;
  disabled?: boolean;
}

export function EmptyState({ project, onSelectPrompt, disabled }: EmptyStateProps) {
  const quickPrompts = [
    {
      title: '项目架构概览',
      hint: '分析当前工作区的核心目录与模块结构',
      prompt: '请分析当前工作区的核心目录结构与主要模块的职责。',
    },
    {
      title: '检查未提交代码',
      hint: '查看 git 状态与近期代码变更',
      prompt: '请查看当前项目的 git status 和近期的修改，并给出总结。',
    },
    {
      title: '查找待办事项',
      hint: '检索项目中的 TODO 和 FIXME 标注',
      prompt: '请检索代码中所有的 TODO 和 FIXME 标记，并列出位置与内容。',
    },
    {
      title: '功能设计与实现',
      hint: '根据需求设计新的接口或模块实现',
      prompt: '我想在当前项目中添加一个新特性，请先帮我梳理实现步骤。',
    },
  ];

  return (
    <div className="empty-state-card">
      <div className="empty-state-icon">
        <SparklesIcon size={28} />
      </div>
      <h2 className="empty-state-title">
        {project ? `已就绪：${project.name}` : '欢迎使用 RinCode Desktop'}
      </h2>
      <p className="empty-state-desc">
        {project
          ? `当前工作区：${project.path}。可以在下方直接提问，或选择快捷指令开始对话。`
          : '请在左侧侧边栏选择或添加项目，即可开启独立的工程智能助手。'}
      </p>

      {project && (
        <div className="quick-prompts-grid">
          {quickPrompts.map((item, idx) => (
            <button
              key={idx}
              type="button"
              className="quick-prompt-card"
              disabled={disabled}
              onClick={() => onSelectPrompt(item.prompt)}
            >
              <div className="quick-prompt-title">{item.title}</div>
              <div className="quick-prompt-hint">{item.hint}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
