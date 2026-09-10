import React, { useEffect, useRef } from 'react';
import type { ChatMessage, ClarifyRequest } from '../types';
import { MessageItem } from './MessageItem';
import { ClarifyModal } from './ClarifyModal';

interface ChatAreaProps {
  messages: ChatMessage[];
  clarifyRequest: ClarifyRequest | null;
  onRespondClarify: (answer: string) => void;
}

export function ChatArea({
  messages,
  clarifyRequest,
  onRespondClarify,
}: ChatAreaProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto scroll to bottom when messages or clarify request update
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, clarifyRequest]);

  return (
    <div className="chat-scroll-area">
      <div className="chat-inner-container">
        {messages.map((msg) => (
          <MessageItem key={msg.id} message={msg} />
        ))}

        {clarifyRequest && (
          <ClarifyModal
            request={clarifyRequest}
            onRespond={onRespondClarify}
          />
        )}
        <div ref={bottomRef} style={{ height: 1 }} />
      </div>
    </div>
  );
}
