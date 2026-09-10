import React, { useState, useEffect, useRef } from 'react';
import { BrainIcon, ChevronDownIcon, ChevronRightIcon } from '../icons';

interface ThinkingBlockProps {
  thinking: string;
  isActive?: boolean;
}

export function ThinkingBlock({ thinking, isActive }: ThinkingBlockProps) {
  // Starts collapsed after completion; active thinking starts expanded
  const [isOpen, setIsOpen] = useState(Boolean(isActive));
  const prevActiveRef = useRef(isActive);

  useEffect(() => {
    if (isActive && !prevActiveRef.current) {
      setIsOpen(true);
    } else if (!isActive && prevActiveRef.current) {
      setIsOpen(false);
    }
    prevActiveRef.current = isActive;
  }, [isActive]);

  if (!thinking && !isActive) return null;

  return (
    <div className="thinking-block">
      <button
        type="button"
        className="thinking-header"
        onClick={() => setIsOpen((prev) => !prev)}
        aria-expanded={isOpen}
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
      </button>
      {isOpen && (
        <div className="thinking-body">
          {thinking || '思考中...'}
          {isActive && <span className="typing-cursor" />}
        </div>
      )}
    </div>
  );
}
