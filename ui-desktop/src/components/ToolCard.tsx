import React, { useState } from 'react';
import type { ToolCallItem } from '../types';
import {
  AlertCircleIcon,
  CheckIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  TerminalIcon,
  ToolIcon,
} from '../icons';
import { safeJsonStringify } from '../utils/format';

interface ToolCardProps {
  tool: ToolCallItem;
}

export function ToolCard({ tool }: ToolCardProps) {
  const [expanded, setExpanded] = useState(false);

  const isBash = tool.name === 'bash' || tool.name === 'run_command';
  const hasDetails = Boolean(tool.args || tool.preview || tool.resultPreview || tool.error || tool.inlineDiff);

  return (
    <div className="tool-card">
      <button
        type="button"
        className={`tool-card-header ${expanded ? 'expanded' : ''}`}
        onClick={() => setExpanded((prev) => !prev)}
        aria-expanded={expanded}
        disabled={!hasDetails}
      >
        <span className="tool-info-left">
          <span className="tool-name-badge">
            {isBash ? <TerminalIcon size={14} /> : <ToolIcon size={14} />}
            <span>{tool.name}</span>
          </span>

          {tool.status === 'running' && (
            <span className="tool-status-pill running">
              <span className="status-dot starting" style={{ width: 6, height: 6 }} />
              <span>执行中</span>
            </span>
          )}
          {tool.status === 'completed' && (
            <span className="tool-status-pill completed">
              <CheckIcon size={11} />
              <span>已完成</span>
            </span>
          )}
          {tool.status === 'failed' && (
            <span className="tool-status-pill failed">
              <AlertCircleIcon size={11} />
              <span>失败</span>
            </span>
          )}
        </span>

        {hasDetails && (
          <span className="tool-card-chevron" aria-hidden="true">
            {expanded ? <ChevronDownIcon size={14} /> : <ChevronRightIcon size={14} />}
          </span>
        )}
      </button>

      {expanded && hasDetails && (
        <div className="tool-card-body">
          {tool.args !== undefined && (
            <div className="tool-detail-section">
              <span className="tool-detail-label">调用参数</span>
              <pre className="tool-detail-box">
                {safeJsonStringify(tool.args)}
              </pre>
            </div>
          )}

          {tool.preview && !tool.resultPreview && (
            <div className="tool-detail-section">
              <span className="tool-detail-label">实时输出</span>
              <pre className="tool-detail-box">{tool.preview}</pre>
            </div>
          )}

          {tool.inlineDiff && (
            <div className="tool-detail-section">
              <span className="tool-detail-label">代码差异 (Diff)</span>
              <pre className="tool-detail-box" style={{ color: 'var(--text-secondary)' }}>
                {tool.inlineDiff}
              </pre>
            </div>
          )}

          {tool.resultPreview && (
            <div className="tool-detail-section">
              <span className="tool-detail-label">
                执行结果 {tool.truncated && <span style={{ color: 'var(--warning)' }}>(已截断)</span>}
              </span>
              <pre className="tool-detail-box">{tool.resultPreview}</pre>
            </div>
          )}

          {tool.error && (
            <div className="tool-detail-section">
              <span className="tool-detail-label" style={{ color: 'var(--danger)' }}>错误信息</span>
              <pre className="tool-detail-box" style={{ color: 'var(--danger)', borderColor: 'var(--danger-border)' }}>
                {tool.error}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
