import React from 'react';
import type { ChatMessage } from '../types';
import { AlertCircleIcon, LogoIcon } from '../icons';
import { MarkdownView } from './MarkdownView';
import { ThinkingBlock } from './ThinkingBlock';
import { ToolCard } from './ToolCard';

interface MessageItemProps {
  message: ChatMessage;
}

export function MessageItem({ message }: MessageItemProps) {
  if (message.role === 'user') {
    return (
      <div className="message-row user">
        <div className="user-bubble">{message.text}</div>
      </div>
    );
  }

  if (message.role === 'tool') {
    return (
      <div className="message-row assistant">
        <div className="assistant-content">
          <ToolCard
            tool={{
              id: message.id,
              name: message.name || 'tool',
              args: message.text,
              status: 'completed',
              startedAt: message.timestamp,
            }}
          />
        </div>
      </div>
    );
  }

  // Assistant message
  return (
    <div className="message-row assistant">
      <div className="assistant-content">
        <div className="assistant-header">
          <LogoIcon size={16} color="var(--accent)" />
          <span>RinCode</span>
        </div>

        {/* Thinking / Reasoning section */}
        {(message.thinking || message.isThinkingActive) && (
          <ThinkingBlock
            thinking={message.thinking || ''}
            isActive={message.isThinkingActive}
          />
        )}

        {/* Tool calls */}
        {message.toolCalls && message.toolCalls.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {message.toolCalls.map((tool) => (
              <ToolCard key={tool.id} tool={tool} />
            ))}
          </div>
        )}

        {/* Assistant markdown text */}
        {message.text ? (
          <div className="assistant-body">
            <MarkdownView content={message.text} />
            {message.isStreaming && <span className="typing-cursor" />}
          </div>
        ) : message.isStreaming && !message.toolCalls?.length && !message.thinking ? (
          <div className="assistant-body" style={{ color: 'var(--text-muted)' }}>
            <span>正在准备回复...</span>
            <span className="typing-cursor" />
          </div>
        ) : null}

        {/* Error notification if turn errored or was cancelled */}
        {message.error && (
          <div className="error-banner">
            <AlertCircleIcon size={16} />
            <span>{message.error}</span>
          </div>
        )}
      </div>
    </div>
  );
}
