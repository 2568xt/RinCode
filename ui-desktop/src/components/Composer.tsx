import React, { useState, useRef, useEffect } from 'react';
import { FolderIcon, SendIcon, StopIcon } from '../icons';
import type { Project } from '../types';

interface ComposerProps {
  onSend: (text: string) => void;
  onCancel: () => void;
  isTurnRunning: boolean;
  disabled: boolean;
  placeholder?: string;
  initialValue?: string;
  project: Project | null;
  modelPicker?: React.ReactNode;
}

export function Composer({
  onSend,
  onCancel,
  isTurnRunning,
  disabled,
  placeholder = '描述你的想法，或交给 RinCode 一个任务…',
  initialValue = '',
  project,
  modelPicker,
}: ComposerProps) {
  const [text, setText] = useState(initialValue);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (initialValue) {
      setText(initialValue);
      if (textareaRef.current) {
        textareaRef.current.focus();
      }
    }
  }, [initialValue]);

  // Auto-resize textarea height
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    const newHeight = Math.min(el.scrollHeight, 180);
    el.style.height = `${Math.max(newHeight, 48)}px`;
  }, [text]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Escape' && isTurnRunning) {
      e.preventDefault();
      onCancel();
      return;
    }

    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    if (e.key === 'Enter' && !e.shiftKey) {
      // Send message
      e.preventDefault();
      if (isTurnRunning) return;
      if (text.trim() && !disabled) {
        onSend(text.trim());
        setText('');
        if (textareaRef.current) {
          textareaRef.current.style.height = '48px';
        }
      }
    }
  };

  const handleSendClick = () => {
    if (isTurnRunning) {
      onCancel();
    } else if (text.trim() && !disabled) {
      onSend(text.trim());
      setText('');
      if (textareaRef.current) {
        textareaRef.current.style.height = '48px';
      }
    }
  };

  return (
    <div className="composer-wrapper">
      <div className="composer-box">
        <textarea
          ref={textareaRef}
          className="composer-textarea"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={disabled ? '请先选择或连接项目...' : placeholder}
          aria-label="消息或任务"
          disabled={disabled && !isTurnRunning}
          rows={1}
        />
        <div className="composer-footer">
          <div className="composer-footer-left">
            <div className="composer-context" title={project?.path}>
              <FolderIcon size={14} />
              <span>{project?.name || '尚未选择项目'}</span>
            </div>
            {modelPicker}
          </div>
          <div className="composer-actions">
            {isTurnRunning ? (
              <button
                type="button"
                className="composer-stop-btn"
                onClick={handleSendClick}
                title="停止生成 (Esc)"
              >
                <StopIcon size={16} />
              </button>
            ) : (
              <button
                type="button"
                className="composer-send-btn"
                onClick={handleSendClick}
                disabled={disabled || !text.trim()}
                title="发送消息 (Enter)"
              >
                <SendIcon size={15} />
              </button>
            )}
          </div>
        </div>
      </div>
      <div className="composer-hint">
        {isTurnRunning ? '正在处理你的任务 · Esc 停止' : 'Enter 发送 · Shift + Enter 换行'}
      </div>
    </div>
  );
}
