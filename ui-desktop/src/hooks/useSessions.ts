import { useState, useEffect, useCallback, useRef } from 'react';
import type { BackendStatus, ChatMessage, Project, SessionInfo, SessionListItem, WireMessage } from '../types';

interface UseSessionsProps {
  activeProject: Project | null;
  backendStatus: BackendStatus | null;
  rpc: (projectId: string, method: string, params?: Record<string, unknown>) => Promise<any>;
  isInteractionBlocked: () => boolean;
  onSessionResumed: (messages: ChatMessage[]) => void;
  onClearMessages: () => void;
}

export function useSessions({
  activeProject,
  backendStatus,
  rpc,
  isInteractionBlocked,
  onSessionResumed,
  onClearMessages,
}: UseSessionsProps) {
  const [sessions, setSessions] = useState<SessionListItem[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [sessionInfo, setSessionInfo] = useState<SessionInfo | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [sessionError, setSessionError] = useState<string | null>(null);

  // Stable callback refs to prevent unnecessary re-renders and infinite effect loops
  const onSessionResumedRef = useRef(onSessionResumed);
  onSessionResumedRef.current = onSessionResumed;
  const onClearMessagesRef = useRef(onClearMessages);
  onClearMessagesRef.current = onClearMessages;

  // Track newly created lazy sessions so we never call session.resume on an unsaved session
  const lazySessionsRef = useRef<Set<string>>(new Set());

  // Request race tokens
  const listTokenRef = useRef(0);
  const resumeTokenRef = useRef(0);

  const prevProjectIdRef = useRef<string | null>(null);

  // Clear project-specific state immediately when project changes
  useEffect(() => {
    const currentProjId = activeProject?.id || null;
    if (prevProjectIdRef.current !== currentProjId) {
      prevProjectIdRef.current = currentProjId;
      // Invalidate pending promises
      listTokenRef.current++;
      resumeTokenRef.current++;
      setSessions([]);
      setActiveSessionId(null);
      setSessionInfo(null);
      setLoading(false);
      setSessionError(null);
      lazySessionsRef.current.clear();
      onClearMessagesRef.current();
    }
  }, [activeProject?.id]);

  // Load session list for active project only when backend is ready
  const loadSessions = useCallback(async (autoSelectFirst = false) => {
    if (!activeProject || backendStatus?.state !== 'ready') {
      return;
    }

    const currentToken = ++listTokenRef.current;
    setLoading(true);
    setSessionError(null);

    try {
      const resp = await rpc(activeProject.id, 'session.list', {});
      if (currentToken !== listTokenRef.current) return; // Stale promise dropped

      const list: SessionListItem[] = resp?.sessions || [];
      list.sort((a, b) => (b.started_at || 0) - (a.started_at || 0));
      setSessions(list);

      if (autoSelectFirst && list.length > 0) {
        setActiveSessionId(list[0].id);
      }
    } catch (err: any) {
      if (currentToken === listTokenRef.current) {
        const msg = err?.message || String(err);
        console.error('Failed to load session list:', err);
        setSessionError(`加载会话列表失败: ${msg}`);
      }
    } finally {
      if (currentToken === listTokenRef.current) {
        setLoading(false);
      }
    }
  }, [activeProject, backendStatus?.state, rpc]);

  // Reload session list when project is ready
  useEffect(() => {
    if (activeProject && backendStatus?.state === 'ready') {
      loadSessions(true);
    }
  }, [activeProject?.id, backendStatus?.state, loadSessions]);

  // Resume active session
  useEffect(() => {
    if (!activeProject || !activeSessionId || backendStatus?.state !== 'ready') {
      return;
    }

    const currentToken = ++resumeTokenRef.current;
    // A just-created session is blank until its first completed turn.
    if (lazySessionsRef.current.has(activeSessionId)) {
      return;
    }
    setLoading(true);
    setSessionError(null);

    rpc(activeProject.id, 'session.resume', { session_id: activeSessionId })
      .then((resp) => {
        if (currentToken !== resumeTokenRef.current) return; // Stale promise dropped
        setSessionInfo(resp?.info || null);
        const wireMsgs: WireMessage[] = resp?.messages || [];
        const chatMsgs: ChatMessage[] = wireMsgs.map((m, idx) => ({
          id: `resumed-${idx}-${Date.now()}`,
          role: m.role,
          text: m.text || '',
          name: m.name,
          timestamp: Date.now(),
        }));
        onSessionResumedRef.current(chatMsgs);
      })
      .catch((err: any) => {
        if (currentToken === resumeTokenRef.current) {
          const msg = err?.message || String(err);
          console.error('Failed to resume session:', err);
          setSessionError(`恢复会话失败: ${msg}`);
          onClearMessagesRef.current();
        }
      })
      .finally(() => {
        if (currentToken === resumeTokenRef.current) {
          setLoading(false);
        }
      });
  }, [activeProject?.id, activeSessionId, backendStatus?.state, rpc]);

  // Create new session
  const createSession = useCallback(async (): Promise<string | null> => {
    if (!activeProject || loading || isInteractionBlocked() || backendStatus?.state !== 'ready') {
      return null;
    }

    setSessionError(null);
    setLoading(true);
    try {
      const resp = await rpc(activeProject.id, 'session.create', {});
      if (resp?.session_id) {
        const newSessionId: string = resp.session_id;
        lazySessionsRef.current.add(newSessionId);
        setSessionInfo(resp.info || null);
        onClearMessagesRef.current();
        setActiveSessionId(newSessionId);

        const newItem: SessionListItem = {
          id: newSessionId,
          title: '新对话',
          message_count: 0,
          preview: '',
          started_at: Date.now() / 1000,
        };
        setSessions((prev) => [newItem, ...prev.filter((s) => s.id !== newSessionId)]);
        return newSessionId;
      }
    } catch (err: any) {
      const msg = err?.message || String(err);
      console.error('Failed to create session:', err);
      setSessionError(`创建新会话失败: ${msg}`);
    } finally {
      setLoading(false);
    }
    return null;
  }, [activeProject, loading, isInteractionBlocked, backendStatus?.state, rpc]);

  // Delete session
  const deleteSession = useCallback(
    async (sessionId: string) => {
      if (!activeProject || isInteractionBlocked() || loading) {
        return;
      }

      setSessionError(null);
      try {
        const resp = await rpc(activeProject.id, 'session.delete', { session_id: sessionId });

        // Only remove if resp.deleted equals sessionId (not cancelled)
        if (resp && resp.deleted === sessionId) {
          lazySessionsRef.current.delete(sessionId);
          setSessions((prev) => {
            const nextList = prev.filter((s) => s.id !== sessionId);
            if (activeSessionId === sessionId) {
              if (nextList.length > 0) {
                setActiveSessionId(nextList[0].id);
              } else {
                setActiveSessionId(null);
                onClearMessagesRef.current();
              }
            }
            return nextList;
          });
        }
      } catch (err: any) {
        const msg = err?.message || String(err);
        console.error('Failed to delete session:', err);
        setSessionError(`删除会话失败: ${msg}`);
      }
    },
    [activeProject, isInteractionBlocked, loading, rpc, activeSessionId]
  );

  const selectSession = useCallback(
    (sessionId: string) => {
      if (isInteractionBlocked() || loading) return;
      if (sessionId === activeSessionId) return;
      onClearMessagesRef.current();
      setActiveSessionId(sessionId);
    },
    [isInteractionBlocked, loading, activeSessionId]
  );

  const updateSessionItem = useCallback(
    (sessionId: string, updater: (item: SessionListItem) => SessionListItem) => {
      lazySessionsRef.current.delete(sessionId);
      setSessions((prev) =>
        prev.map((s) => (s.id === sessionId ? updater(s) : s))
      );
    },
    []
  );

  const clearSessionError = useCallback(() => setSessionError(null), []);

  return {
    sessions,
    activeSessionId,
    sessionInfo,
    loading,
    sessionError,
    clearSessionError,
    selectSession,
    createSession,
    deleteSession,
    loadSessions,
    updateSessionItem,
  };
}
