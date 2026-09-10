import type { BackendStatus, DesktopBridge, DesktopEvent, Project } from './bridge';

export type { BackendStatus, DesktopBridge, DesktopEvent, Project };

export interface SessionListItem {
  id: string;
  message_count: number;
  preview: string;
  source?: string | null;
  started_at: number;
  title: string;
}

export interface SessionInfo {
  model?: string;
  skills?: Record<string, string[]>;
  tools?: Record<string, string[]>;
  lazy?: boolean;
  [key: string]: unknown;
}

export interface WireMessage {
  role: 'user' | 'assistant' | 'system' | 'tool';
  text?: string;
  name?: string;
  context?: unknown;
}

export interface ToolCallItem {
  id: string;
  name: string;
  args?: unknown;
  preview?: string;
  resultPreview?: string;
  status: 'running' | 'completed' | 'failed';
  truncated?: boolean;
  error?: string;
  inlineDiff?: string;
  startedAt: number;
  completedAt?: number;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  text: string;
  thinking?: string;
  isThinkingActive?: boolean;
  isStreaming?: boolean;
  toolCalls?: ToolCallItem[];
  error?: string;
  timestamp: number;
  name?: string;
}

export interface ConfirmRequest {
  requestId: string;
  prompt: string;
  defaultAnswer: boolean;
}

export interface ClarifyRequest {
  requestId: string;
  question: string;
  choices: string[] | null;
  conversationId?: string;
}

export interface TurnEventPayload {
  type: string;
  payload?: any;
}
