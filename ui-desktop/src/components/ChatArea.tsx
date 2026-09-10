import React, { useEffect, useRef } from 'react';
import type { ChatMessage, ClarifyRequest, Project } from '../types';
import { EmptyState } from './EmptyState';
import { MessageItem } from './MessageItem';
import { ClarifyModal } from './ClarifyModal';

interface ChatAreaProps {
  project: Project | null;
  messages: ChatMessage[];
  clarifyRequest: ClarifyRequest | null;
  onRespondClarify: (answer: string) => void;
  onSelectPrompt: (prompt: string) => void;
  isTurnRunning: boolean;
}

export function ChatArea({
  project,
  messages,
  clarifyRequest,
  onRespondClarify,
  onSelectPrompt,
  isTurnRunning,
}: ChatAreaProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto scroll to bottom when messages or clarify request update
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, clarifyRequest]);

  return (
    <div className="chat-scroll-area">
      <div className="chat-inner-container">
        {messages.length === 0 ? (
          <EmptyState
            project={project}
            onSelectPrompt={onSelectPrompt}
            disabled={isTurnRunning}
          />
        ) : (
          <>
            {messages.map((msg) => (
              <MessageItem key={msg.id} message={msg} />
            ))}

            {clarifyRequest && (
              <ClarifyModal
                request={clarifyRequest}
                onRespond={onRespondClarify}
              />
            )}
          </>
        )}
        <div ref={bottomRef} style={{ height: 1 }} />
      </div>
    </div>
  );
}
