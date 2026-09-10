import React, { useState, useEffect } from 'react';
import { BrainIcon, ChevronDownIcon, ChevronRightIcon } from '../icons';

interface ThinkingBlockProps {
  thinking: string;
  isActive?: boolean;
}

export function ThinkingBlock({ thinking, isActive }: ThinkingBlockProps) {
  // If active, keep open by default; user can also toggle
  const [isOpen, setIsOpen] = useState(true);

  useEffect(() => {
    if (isActive) {
      setIsOpen(true);
    }
  }, [isActive]);

  if (!thinking && !isActive) return null;

  return (
    <div className="thinking-block">
      <div
        className="thinking-header"
        onClick={() => setIsOpen((prev) => !prev)}
      >
        <div className="thinking-title">
          <BrainIcon size={14} />
          <span>思考过程</span>
          {isActive ? (
            <span className="thinking-badge-active">正在思考...</span>
          ) : (
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              (点击{isOpen ? '收起' : '展开'})
            </span>
          )}
        </div>
        {isOpen ? <ChevronDownIcon size={14} /> : <ChevronRightIcon size={14} />}
      </div>
      {isOpen && (
        <div className="thinking-body">
          {thinking || '思考中...'}
          {isActive && <span className="typing-cursor" />}
        </div>
      )}
    </div>
  );
}
