import React, { useState } from 'react';
import type { ClarifyRequest } from '../types';
import { HelpCircleIcon } from '../icons';

interface ClarifyModalProps {
  request: ClarifyRequest;
  onRespond: (answer: string) => void;
}

export function ClarifyModal({ request, onRespond }: ClarifyModalProps) {
  const [customAnswer, setCustomAnswer] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (customAnswer.trim()) {
      onRespond(customAnswer.trim());
    }
  };

  return (
    <div className="clarify-card">
      <div className="clarify-question-title">
        <HelpCircleIcon size={18} />
        <span>智能体向您提问：</span>
      </div>
      <div style={{ fontSize: 14, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
        {request.question}
      </div>

      {request.choices && request.choices.length > 0 && (
        <div className="clarify-choices-grid">
          {request.choices.map((choice, idx) => (
            <button
              key={idx}
              type="button"
              className="clarify-choice-btn"
              onClick={() => onRespond(choice)}
            >
              {choice}
            </button>
          ))}
        </div>
      )}

      <form onSubmit={handleSubmit} className="clarify-input-row">
        <input
          type="text"
          className="clarify-input"
          placeholder="输入您的回答..."
          value={customAnswer}
          onChange={(e) => setCustomAnswer(e.target.value)}
          autoFocus
        />
        <button
          type="submit"
          className="clarify-submit-btn"
          disabled={!customAnswer.trim()}
        >
          提交回答
        </button>
      </form>
    </div>
  );
}
