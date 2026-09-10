import React from 'react';
import type { ConfirmRequest } from '../types';
import { AlertCircleIcon } from '../icons';

interface ConfirmModalProps {
  request: ConfirmRequest;
  onRespond: (answer: boolean) => void;
}

export function ConfirmModal({ request, onRespond }: ConfirmModalProps) {
  return (
    <div className="modal-backdrop">
      <div className="modal-dialog">
        <div className="modal-header">
          <AlertCircleIcon size={20} color="var(--danger)" />
          <span>确认删除会话</span>
        </div>
        <div className="modal-body">
          <p>{request.prompt || '确定要删除该会话记录吗？此操作无法撤销。'}</p>
        </div>
        <div className="modal-footer">
          <button
            type="button"
            className="btn-secondary"
            onClick={() => onRespond(false)}
          >
            取消
          </button>
          <button
            type="button"
            className="btn-danger"
            onClick={() => onRespond(true)}
          >
            确认删除
          </button>
        </div>
      </div>
    </div>
  );
}
