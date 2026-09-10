import React from 'react';
import type { BackendStatus } from '../types';

interface StatusIndicatorProps {
  status: BackendStatus | null;
}

export function StatusIndicator({ status }: StatusIndicatorProps) {
  const state = status?.state || 'stopped';
  const message = status?.message;

  let label = '已停止';
  if (state === 'ready') label = '已就绪';
  else if (state === 'starting') label = '启动中...';
  else if (state === 'error') label = '服务异常';

  return (
    <div
      className="status-pill"
      title={message ? `${label}: ${message}` : label}
    >
      <span className={`status-dot ${state}`} />
      <span>{label}</span>
    </div>
  );
}
