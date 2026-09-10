import React, { useState, useCallback, useRef } from 'react';
import { useDesktopBridge } from './hooks/useDesktopBridge';
import { useSessions } from './hooks/useSessions';
import { useTurn } from './hooks/useTurn';
import { Sidebar } from './components/Sidebar';
import { Header } from './components/Header';
import { ChatArea } from './components/ChatArea';
import { Composer } from './components/Composer';
import { ConfirmModal } from './components/ConfirmModal';
import { XIcon } from './icons';
import { formatTruncated } from './utils/format';
import type { ChatMessage, Project } from './types';

export function App() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [composerDraft, setComposerDraft] = useState('');

  // 1. Desktop Bridge connection & events
  const {
    projects,
    activeProject,
    setActiveProject,
    backendStatus,
    bridgeError,
    clearBridgeError,
    addProject,
    rpc,
    addEventListener,
  } = useDesktopBridge();

  const setResumedMessagesRef = useRef<((msgs: ChatMessage[]) => void) | null>(null);
  const clearMessagesRef = useRef<(() => void) | null>(null);
  const isTurnRunningRef = useRef(false);
  const isConfirmPendingRef = useRef(false);

  const isInteractionBlocked = useCallback(
    () => isTurnRunningRef.current || isConfirmPendingRef.current, []
  );

  // 2. Sessions management
  const {
    sessions,
    activeSessionId,
    loading: sessionLoading,
    sessionError,
    clearSessionError,
    selectSession,
    createSession,
    deleteSession,
    updateSessionItem,
  } = useSessions({
    activeProject,
    backendStatus,
    rpc,
    isInteractionBlocked,
    onSessionResumed: (msgs) => setResumedMessagesRef.current?.(msgs),
    onClearMessages: () => clearMessagesRef.current?.(),
  });

  // 3. Turn execution & streaming state
  const {
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
  } = useTurn({
    activeProject,
    activeSessionId,
    backendStatus,
    rpc,
    addEventListener,
    onTurnCompleted: (sessionId, _userText, assistantText) => {
      updateSessionItem(sessionId, (s) => ({
        ...s,
        message_count: s.message_count + 2,
        preview: assistantText ? formatTruncated(assistantText.replace(/\s+/g, ' '), 40) : s.preview,
      }));
    },
  });

  isTurnRunningRef.current = isTurnRunning;
  isConfirmPendingRef.current = Boolean(confirmRequest);
  setResumedMessagesRef.current = setResumedMessages;
  clearMessagesRef.current = clearMessages;

  const isConfirmPending = Boolean(confirmRequest);
  const isBusy = isTurnRunning || isConfirmPending || sessionLoading;

  // Send turn handler: passes explicit targetSessionId to avoid stale closure during first send
  const handleSend = useCallback(
    async (text: string) => {
      if (!activeProject || sessionLoading || isTurnRunning || isConfirmPending || backendStatus?.state !== 'ready') {
        return;
      }

      let targetSessionId = activeSessionId;
      if (!targetSessionId) {
        targetSessionId = await createSession();
      }

      if (targetSessionId) {
        setComposerDraft('');
        sendTurn(text, targetSessionId);
      }
    },
    [activeProject, sessionLoading, isTurnRunning, isConfirmPending, backendStatus?.state, activeSessionId, createSession, sendTurn]
  );

  const handleSelectPrompt = useCallback(
    (promptText: string) => {
      if (isBusy) return;
      handleSend(promptText);
    },
    [isBusy, handleSend]
  );

  const handleSelectProject = useCallback(
    (p: Project) => {
      if (isBusy) return;
      setActiveProject(p);
    },
    [isBusy, setActiveProject]
  );

  const activeSession = sessions.find((s) => s.id === activeSessionId);
  const sessionTitle = activeSession
    ? activeSession.title
    : (sessions.length > 0 ? '选择或新建会话' : '新对话');

  const isComposerDisabled =
    !activeProject ||
    backendStatus?.state !== 'ready' ||
    sessionLoading || isConfirmPending;

  const activeError = bridgeError || sessionError || turnError;

  return (
    <div className="app-container">
      {/* Slim project/session sidebar */}
      <Sidebar
        collapsed={sidebarCollapsed}
        projects={projects}
        activeProject={activeProject}
        onSelectProject={handleSelectProject}
        onAddProject={addProject}
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSelectSession={selectSession}
        onNewSession={createSession}
        onDeleteSession={deleteSession}
        isTurnRunning={isTurnRunning}
        isConfirmPending={isConfirmPending}
        loading={sessionLoading}
      />

      {/* Main chat layout */}
      <main className="main-wrapper">
        <Header
          sidebarCollapsed={sidebarCollapsed}
          onToggleSidebar={() => setSidebarCollapsed((prev) => !prev)}
          project={activeProject}
          sessionTitle={sessionTitle}
          backendStatus={backendStatus}
        />

        {/* Global Error Banner */}
        {activeError && (
          <div
            style={{
              padding: '8px 16px',
              backgroundColor: 'var(--danger-bg)',
              borderBottom: '1px solid var(--danger-border)',
              color: 'var(--danger)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              fontSize: 12,
              zIndex: 10,
            }}
          >
            <span>{activeError}</span>
            <button
              type="button"
              onClick={() => {
                clearBridgeError();
                clearSessionError();
                clearTurnError();
              }}
              style={{ color: 'var(--danger)', padding: 4 }}
              title="关闭提示"
            >
              <XIcon size={14} />
            </button>
          </div>
        )}

        <ChatArea
          project={activeProject}
          messages={messages}
          clarifyRequest={clarifyRequest}
          onRespondClarify={respondClarify}
          onSelectPrompt={handleSelectPrompt}
          isTurnRunning={isTurnRunning}
        />

        <Composer
          onSend={handleSend}
          onCancel={cancelTurn}
          isTurnRunning={isTurnRunning}
          disabled={isComposerDisabled}
          initialValue={composerDraft}
        />
      </main>

      {/* Real Top-level Confirm Dialog for confirm.request (session deletion) */}
      {confirmRequest && (
        <ConfirmModal
          request={confirmRequest}
          onRespond={respondConfirm}
        />
      )}
    </div>
  );
}
