import React, { useLayoutEffect, useRef } from 'react';
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
  const scrollRef = useRef<HTMLDivElement>(null);
  const followBottomRef = useRef(true);

  // Follow new output only while the reader stays near the bottom.
  useLayoutEffect(() => {
    const viewport = scrollRef.current;
    if (viewport && followBottomRef.current) {
      viewport.scrollTop = viewport.scrollHeight;
    }
  }, [messages, clarifyRequest]);

  return (
    <div className="chat-scroll-area" ref={scrollRef} onScroll={event => {
      const viewport = event.currentTarget;
      followBottomRef.current = viewport.scrollHeight - viewport.clientHeight - viewport.scrollTop <= 48;
    }}>
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
        <div style={{ height: 1 }} />
      </div>
    </div>
  );
}
