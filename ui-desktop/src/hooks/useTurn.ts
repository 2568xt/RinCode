import { useState, useCallback, useEffect, useRef } from 'react';
import type {
  BackendStatus,
  ChatMessage,
  ClarifyRequest,
  ConfirmRequest,
  DesktopEvent,
  Project,
  ToolCallItem,
} from '../types';

interface UseTurnProps {
  activeProject: Project | null;
  activeSessionId: string | null;
  backendStatus: BackendStatus | null;
  rpc: (projectId: string, method: string, params?: Record<string, unknown>) => Promise<any>;
  addEventListener: (fn: (event: DesktopEvent) => void) => () => void;
  onTurnCompleted?: (sessionId: string, lastUserText: string, lastAssistantText: string) => void;
}

export function useTurn({
  activeProject,
  activeSessionId,
  backendStatus,
  rpc,
  addEventListener,
  onTurnCompleted,
}: UseTurnProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isTurnRunning, setIsTurnRunning] = useState<boolean>(false);
  const [confirmRequest, setConfirmRequest] = useState<ConfirmRequest | null>(null);
  const [clarifyRequest, setClarifyRequest] = useState<ClarifyRequest | null>(null);
  const [turnError, setTurnError] = useState<string | null>(null);

  // Stable refs
  const activeSubscriptionIdRef = useRef<string | null>(null);
  const subscribedProjectIdRef = useRef<string | null>(null);
  const subscribedSessionIdRef = useRef<string | null>(null);
  const currentTurnSessionIdRef = useRef<string | null>(null);

  const activeProjectRef = useRef<Project | null>(activeProject);
  activeProjectRef.current = activeProject;
  const activeSessionIdRef = useRef<string | null>(activeSessionId);
  activeSessionIdRef.current = activeSessionId;
  const isTurnRunningRef = useRef<boolean>(isTurnRunning);
  isTurnRunningRef.current = isTurnRunning;

  const onTurnCompletedRef = useRef(onTurnCompleted);
  onTurnCompletedRef.current = onTurnCompleted;

  const lastUserTextRef = useRef<string>('');
  const lastAssistantAccumulatedRef = useRef<string>('');

  // Unsubscribe previous subscription safely using captured project ID
  const unsubscribeCurrent = useCallback(async () => {
    const subId = activeSubscriptionIdRef.current;
    const projId = subscribedProjectIdRef.current;
    activeSubscriptionIdRef.current = null;
    subscribedProjectIdRef.current = null;
    subscribedSessionIdRef.current = null;
    if (subId && projId) {
      try {
        await rpc(projId, 'turn.unsubscribe', { subscription_id: subId });
      } catch {
        // Safe ignore
      }
    }
  }, [rpc]);

  // Clean up when project changes or unmounts
  useEffect(() => {
    return () => {
      unsubscribeCurrent();
    };
  }, [unsubscribeCurrent]);

  // When activeSessionId changes, only unsubscribe if it doesn't match the current running turn's session
  useEffect(() => {
    if (isTurnRunningRef.current && activeSessionId && activeSessionId === currentTurnSessionIdRef.current) {
      // Don't clear a just-started turn in this session
      return;
    }
    unsubscribeCurrent();
    setIsTurnRunning(false);
    setClarifyRequest(null);
  }, [activeSessionId, activeProject?.id, unsubscribeCurrent]);

  // If backend disconnects or is not ready, clear active running turn and clarification
  useEffect(() => {
    if (backendStatus && backendStatus.state !== 'ready') {
      if (isTurnRunningRef.current) {
        setIsTurnRunning(false);
      }
      setClarifyRequest(null);
      setConfirmRequest(null);
    }
  }, [backendStatus?.state]);

  // Handle incoming desktop events
  useEffect(() => {
    const handleDesktopEvent = (event: DesktopEvent) => {
      // 1. Filter by active project
      if (!activeProjectRef.current || event.projectId !== activeProjectRef.current.id) {
        return;
      }

      // 2. Handle top-level notifications
      if (event.method === 'confirm.request') {
        const params = event.params || {};
        setConfirmRequest({
          requestId: params.request_id || '',
          prompt: params.prompt || '',
          defaultAnswer: Boolean(params.default),
        });
        return;
      }

      if (event.method === 'clarify.request') {
        const params = event.params || {};
        setClarifyRequest({
          requestId: params.request_id || '',
          question: params.question || '',
          choices: Array.isArray(params.choices) ? params.choices : null,
          conversationId: params.conversation_id,
        });
        return;
      }

      // 3. Handle stream events: method === 'event'
      if (event.method === 'event') {
        const params = event.params || {};
        const subId = params.subscription_id;

        // Filter subscription ID to prevent stale or cross-session event leakage
        if (!activeSubscriptionIdRef.current || subId !== activeSubscriptionIdRef.current) {
          return;
        }

        const innerEvent = params.event || {};
        const eventType: string = innerEvent.type;
        const payload = innerEvent.payload || {};

        if (eventType === 'token.delta') {
          const deltaText: string = payload.text || '';
          lastAssistantAccumulatedRef.current += deltaText;
          setMessages((prev) => {
            const list = [...prev];
            const last = list[list.length - 1];
            if (last && last.role === 'assistant') {
              list[list.length - 1] = {
                ...last,
                text: last.text + deltaText,
                isStreaming: true,
              };
            }
            return list;
          });
        } else if (eventType === 'thinking.delta' || eventType === 'reasoning.delta') {
          const deltaThinking: string = payload.text || '';
          setMessages((prev) => {
            const list = [...prev];
            const last = list[list.length - 1];
            if (last && last.role === 'assistant') {
              list[list.length - 1] = {
                ...last,
                thinking: (last.thinking || '') + deltaThinking,
                isThinkingActive: true,
              };
            }
            return list;
          });
        } else if (eventType === 'tool.start') {
          const toolId: string = payload.tool_call_id || payload.tool_id || `tool-${Date.now()}`;
          const newTool: ToolCallItem = {
            id: toolId,
            name: payload.name || 'tool',
            args: payload.arguments ?? payload.context,
            status: 'running',
            startedAt: Date.now(),
          };
          setMessages((prev) => {
            const list = [...prev];
            const last = list[list.length - 1];
            if (last && last.role === 'assistant') {
              const toolCalls = [...(last.toolCalls || []), newTool];
              list[list.length - 1] = {
                ...last,
                isThinkingActive: false,
                toolCalls,
              };
            }
            return list;
          });
        } else if (eventType === 'tool.progress') {
          const toolId: string = payload.tool_call_id || payload.tool_id || '';
          setMessages((prev) => {
            const list = [...prev];
            const last = list[list.length - 1];
            if (last && last.role === 'assistant' && last.toolCalls) {
              const toolCalls = last.toolCalls.map((t) =>
                t.id === toolId ? { ...t, preview: payload.preview || t.preview } : t
              );
              list[list.length - 1] = { ...last, toolCalls };
            }
            return list;
          });
        } else if (eventType === 'tool.complete') {
          const toolId: string = payload.tool_call_id || payload.tool_id || '';
          setMessages((prev) => {
            const list = [...prev];
            const last = list[list.length - 1];
            if (last && last.role === 'assistant' && last.toolCalls) {
              const toolCalls = last.toolCalls.map((t) =>
                t.id === toolId
                  ? {
                      ...t,
                      status: (payload.failed ? 'failed' : 'completed') as 'completed' | 'failed',
                      resultPreview: payload.result_preview || payload.summary || payload.error,
                      truncated: Boolean(payload.truncated),
                      error: payload.error,
                      inlineDiff: payload.inline_diff,
                      completedAt: Date.now(),
                    }
                  : t
              );
              list[list.length - 1] = { ...last, toolCalls };
            }
            return list;
          });
        } else if (eventType === 'message.start') {
          setMessages((prev) => {
            const list = [...prev];
            const last = list[list.length - 1];
            if (last && last.role === 'assistant') {
              list[list.length - 1] = {
                ...last,
                isStreaming: true,
              };
            }
            return list;
          });
        } else if (eventType === 'message.complete') {
          setIsTurnRunning(false);
          const finalAssistantText = payload.text || lastAssistantAccumulatedRef.current;
          setMessages((prev) => {
            const list = [...prev];
            const last = list[list.length - 1];
            if (last && last.role === 'assistant') {
              list[list.length - 1] = {
                ...last,
                isStreaming: false,
                isThinkingActive: false,
                text: payload.text || last.text,
              };
            }
            return list;
          });

          // PURE: Call side effect outside setMessages updater
          if (currentTurnSessionIdRef.current && onTurnCompletedRef.current) {
            onTurnCompletedRef.current(
              currentTurnSessionIdRef.current,
              lastUserTextRef.current,
              finalAssistantText
            );
          }
        } else if (eventType === 'error') {
          setIsTurnRunning(false);
          const isCancelled = payload.reason === 'cancelled_by_client';
          const errMsg = isCancelled
            ? '已由用户停止生成'
            : payload.message || '执行过程出现错误';
          setMessages((prev) => {
            const list = [...prev];
            const last = list[list.length - 1];
            if (last && last.role === 'assistant') {
              list[list.length - 1] = {
                ...last,
                isStreaming: false,
                isThinkingActive: false,
                error: errMsg,
              };
            }
            return list;
          });
        }
      }
    };

    return addEventListener(handleDesktopEvent);
  }, [addEventListener]);

  // Send turn with optional overrideSessionId to avoid stale closure during first send
  const sendTurn = useCallback(
    async (content: string, overrideSessionId?: string) => {
      const targetSessionId = overrideSessionId || activeSessionIdRef.current;
      const proj = activeProjectRef.current;

      if (!proj || !targetSessionId || isTurnRunningRef.current || !content.trim()) {
        return;
      }

      currentTurnSessionIdRef.current = targetSessionId;
      lastUserTextRef.current = content;
      lastAssistantAccumulatedRef.current = '';
      setTurnError(null);

      // Add user message and assistant placeholder
      const userMessageId = `user-${Date.now()}`;
      const assistantMessageId = `assistant-${Date.now() + 1}`;

      const userMsg: ChatMessage = {
        id: userMessageId,
        role: 'user',
        text: content,
        timestamp: Date.now(),
      };
      const assistantMsg: ChatMessage = {
        id: assistantMessageId,
        role: 'assistant',
        text: '',
        isStreaming: true,
        isThinkingActive: false,
        timestamp: Date.now() + 1,
      };

      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setIsTurnRunning(true);

      try {
        // Unsubscribe old subscription first if any
        await unsubscribeCurrent();

        // 1. Subscribe FIRST
        const subResp = await rpc(proj.id, 'turn.subscribe', {
          session_key: targetSessionId,
        });

        if (!subResp?.subscription_id) {
          throw new Error('turn.subscribe 未返回有效 subscription_id');
        }

        activeSubscriptionIdRef.current = subResp.subscription_id;
        subscribedProjectIdRef.current = proj.id;
        subscribedSessionIdRef.current = targetSessionId;

        // 2. Send turn
        const sendResp = await rpc(proj.id, 'turn.send', {
          session_key: targetSessionId,
          content,
        });

        // Never infer completion from turn.send accepted!
        if (!sendResp?.accepted) {
          throw new Error('后端拒绝接纳当前 Turn 请求');
        }
      } catch (err: any) {
        setIsTurnRunning(false);
        const errMsg = err?.message || String(err);
        setTurnError(`发送失败: ${errMsg}`);
        setMessages((prev) => {
          const list = [...prev];
          const last = list[list.length - 1];
          if (last && last.id === assistantMessageId) {
            list[list.length - 1] = {
              ...last,
              isStreaming: false,
              error: `发送失败: ${errMsg}`,
            };
          }
          return list;
        });
      }
    },
    [rpc, unsubscribeCurrent]
  );

  // Cancel turn
  const cancelTurn = useCallback(async () => {
    const proj = activeProjectRef.current;
    const sessId = currentTurnSessionIdRef.current || activeSessionIdRef.current;
    if (!proj || !sessId || !isTurnRunningRef.current) return;

    try {
      setTurnError(null);
      await rpc(proj.id, 'turn.cancel', { session_key: sessId });
    } catch (err: any) {
      const msg = err?.message || String(err);
      console.error('Failed to cancel turn:', err);
      setTurnError(`取消任务失败: ${msg}`);
    }
  }, [rpc]);

  // Respond to confirm.request (session deletion)
  const respondConfirm = useCallback(
    async (answer: boolean) => {
      const proj = activeProjectRef.current;
      if (!proj || !confirmRequest) return;

      const reqId = confirmRequest.requestId;
      setConfirmRequest(null);
      try {
        setTurnError(null);
        await rpc(proj.id, 'confirm.respond', {
          request_id: reqId,
          answer,
        });
      } catch (err: any) {
        const msg = err?.message || String(err);
        console.error('Failed to respond to confirm:', err);
        setTurnError(`确认响应失败: ${msg}`);
      }
    },
    [confirmRequest, rpc]
  );

  // Respond to clarify.request (ask_user)
  const respondClarify = useCallback(
    async (answer: string) => {
      const proj = activeProjectRef.current;
      if (!proj || !clarifyRequest) return;

      const reqId = clarifyRequest.requestId;
      setClarifyRequest(null);
      try {
        setTurnError(null);
        await rpc(proj.id, 'clarify.respond', {
          request_id: reqId,
          answer,
        });
      } catch (err: any) {
        const msg = err?.message || String(err);
        console.error('Failed to respond to clarify:', err);
        setTurnError(`澄清响应失败: ${msg}`);
      }
    },
    [clarifyRequest, rpc]
  );

  const setResumedMessages = useCallback((msgs: ChatMessage[]) => {
    setMessages(msgs);
  }, []);

  const clearMessages = useCallback(() => {
    setMessages([]);
    lastAssistantAccumulatedRef.current = '';
    lastUserTextRef.current = '';
  }, []);

  const clearTurnError = useCallback(() => setTurnError(null), []);

  return {
    messages,
    isTurnRunning,
    confirmRequest,
    clarifyRequest,
    turnError,
    clearTurnError,
    sendTurn,
    cancelTurn,
    respondConfirm,
    respondClarify,
    setResumedMessages,
    clearMessages,
  };
}
