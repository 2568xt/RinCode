import React, { useState, useCallback, useRef } from 'react';
import { useDesktopBridge } from './hooks/useDesktopBridge';
import { useSessions } from './hooks/useSessions';
import { useTurn } from './hooks/useTurn';
import { Sidebar } from './components/Sidebar';
import { Header } from './components/Header';
import { ChatArea } from './components/ChatArea';
import { Composer } from './components/Composer';
import { EmptyState, QuickPrompts } from './components/EmptyState';
import { ConfirmModal } from './components/ConfirmModal';
import { ModelPicker } from './components/ModelPicker';
import { useModels } from './hooks/useModels';
import { useProjectHistory } from './hooks/useProjectHistory';
import { ConversationLibrary } from './components/ConversationLibrary';
import { XIcon } from './icons';
import { formatTruncated } from './utils/format';
import type { ChatMessage, Project } from './types';

export function App() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [composerDraft, setComposerDraft] = useState('');
  const [requestedSession, setRequestedSession] = useState<{ projectId: string; sessionId: string } | null>(null);
  const [library, setLibrary] = useState<'all' | 'archived' | null>(null);
  const [isManaging, setIsManaging] = useState(false);
  const [managementError, setManagementError] = useState<string | null>(null);
  const managingRef = useRef(false);

  // 1. Desktop Bridge connection & events
  const {
    projects,
    activeProject,
    setActiveProject,
    backendStatus,
    bridgeError,
    clearBridgeError,
    addProject,
    removeProject,
    rpc,
    addEventListener,
  } = useDesktopBridge();

  const { history, loading: historyLoading, error: historyError, reload: reloadHistory } = useProjectHistory(projects);

  const setResumedMessagesRef = useRef<((msgs: ChatMessage[]) => void) | null>(null);
  const clearMessagesRef = useRef<(() => void) | null>(null);
  const isTurnRunningRef = useRef(false);
  const isConfirmPendingRef = useRef(false);
  const sessionLoadingRef = useRef(false);

  const isModelSwitchBlocked = useCallback(
    () => isTurnRunningRef.current || isConfirmPendingRef.current || sessionLoadingRef.current || managingRef.current,
    []
  );

  // 2. Models management
  const {
    options: modelOptions,
    loading: modelLoading,
    error: modelError,
    isSwitching: isSwitchingModel,
    switchError: modelSwitchError,
    reloadOptions: reloadModelOptions,
    selectModel,
    isSwitchingRef: hookIsSwitchingRef,
  } = useModels({
    activeProject,
    backendStatus,
    rpc,
    isInteractionBlocked: isModelSwitchBlocked,
  });

  // Read the model hook's synchronous lock before starting another operation.
  const isInteractionBlocked = useCallback(
    () =>
      isTurnRunningRef.current ||
      isConfirmPendingRef.current ||
      hookIsSwitchingRef.current || managingRef.current,
    [hookIsSwitchingRef]
  );

  // 3. Sessions management
  const {
    sessions: currentSessions,
    activeSessionId,
    loading: sessionLoading,
    sessionError,
    clearSessionError,
    selectSession,
    createSession,
    deleteSession,
    updateSessionItem,
    loadSessions,
  } = useSessions({
    activeProject,
    backendStatus,
    rpc,
    isInteractionBlocked,
    onSessionResumed: (msgs) => setResumedMessagesRef.current?.(msgs),
    onClearMessages: () => clearMessagesRef.current?.(),
    preferredSessionId: requestedSession?.projectId === activeProject?.id ? requestedSession?.sessionId : null,
    historyReady: Boolean(activeProject && history[activeProject.id]),
    archivedSessionIds: activeProject ? history[activeProject.id]?.sessions.filter(s => s.archived).map(s => s.id) : [],
  });

  const savedSessions = activeProject ? history[activeProject.id]?.sessions : undefined;
  const sessions = currentSessions.map(session => ({
    ...session,
    title: session.title || savedSessions?.find(saved => saved.id === session.id)?.title || '新对话',
    archived: savedSessions?.find(saved => saved.id === session.id)?.archived || false,
  }));

  sessionLoadingRef.current = sessionLoading;

  // 4. Turn execution & streaming state
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
    onTurnCompleted: (sessionId, userText, assistantText) => {
      updateSessionItem(sessionId, (s) => ({
        ...s,
        title: s.title && s.title !== '新对话' ? s.title : formatTruncated(userText.replace(/\s+/g, ' '), 40),
        message_count: s.message_count + 2,
        preview: assistantText ? formatTruncated(assistantText.replace(/\s+/g, ' '), 40) : s.preview,
      }));
      void reloadHistory();
    },
  });

  isTurnRunningRef.current = isTurnRunning;
  isConfirmPendingRef.current = Boolean(confirmRequest);
  setResumedMessagesRef.current = setResumedMessages;
  clearMessagesRef.current = clearMessages;

  const isConfirmPending = Boolean(confirmRequest);
  const isBusy = isTurnRunning || isConfirmPending || sessionLoading || isSwitchingModel || isManaging;
  const activeArchived = Boolean(sessions.find(s => s.id === activeSessionId)?.archived);

  // Send turn handler: passes explicit targetSessionId to avoid stale closure during first send
  const handleSend = useCallback(
    async (text: string) => {
      if (!activeProject || activeArchived || sessionLoadingRef.current || isInteractionBlocked() || backendStatus?.state !== 'ready') {
        return;
      }

      let targetSessionId = activeSessionId;
      if (!targetSessionId) {
        sessionLoadingRef.current = true;
        try {
          targetSessionId = await createSession();
        } finally {
          sessionLoadingRef.current = false;
        }
      }

      if (targetSessionId) {
        setComposerDraft('');
        isTurnRunningRef.current = true;
        sendTurn(text, targetSessionId);
      }
    },
    [activeProject, activeArchived, backendStatus?.state, activeSessionId, createSession, sendTurn, isInteractionBlocked]
  );

  const handleSelectPrompt = useCallback(
    (promptText: string) => {
      if (isBusy || hookIsSwitchingRef.current) return;
      handleSend(promptText);
    },
    [isBusy, handleSend, hookIsSwitchingRef]
  );

  const handleSelectProject = useCallback(
    (p: Project) => {
      if (isBusy || hookIsSwitchingRef.current) return;
      setRequestedSession(null);
      setActiveProject(p);
    },
    [isBusy, setActiveProject, hookIsSwitchingRef]
  );

  const handleSelectProjectSession = useCallback((project: Project, sessionId: string) => {
    if (isBusy || hookIsSwitchingRef.current) return;
    if (project.id === activeProject?.id) {
      selectSession(sessionId);
    } else {
      setRequestedSession({ projectId: project.id, sessionId });
      setActiveProject(project);
    }
  }, [isBusy, hookIsSwitchingRef, activeProject?.id, selectSession, setActiveProject]);

  const handleDeleteSession = useCallback(async (sessionId: string) => {
    await deleteSession(sessionId);
    void reloadHistory();
  }, [deleteSession, reloadHistory]);

  const handleReloadHistory = useCallback(() => {
    void reloadHistory();
    if (!isBusy) void loadSessions();
  }, [reloadHistory, loadSessions, isBusy]);

  const handleRemoveProject = useCallback(async (project: Project) => {
    if (isBusy || managingRef.current) return;
    managingRef.current = true;
    setIsManaging(true);
    setManagementError(null);
    try {
      const removed = await removeProject(project);
      if (removed && project.id === activeProject?.id) setRequestedSession(null);
    } catch { setManagementError('移除项目失败，请重试'); }
    finally { managingRef.current = false; setIsManaging(false); }
  }, [isBusy, removeProject, activeProject?.id]);

  const handleArchiveSession = useCallback(async (project: Project, sessionId: string, archived = true) => {
    if (isBusy || managingRef.current) return false;
    managingRef.current = true;
    setIsManaging(true);
    setManagementError(null);
    try {
      await window.rincode.setSessionArchived(project.id, sessionId, archived);
      await reloadHistory();
      return true;
    } catch { setManagementError(archived ? '归档失败，请重试' : '取消归档失败，请重试'); return false; }
    finally { managingRef.current = false; setIsManaging(false); }
  }, [isBusy, reloadHistory]);

  const closeLibrary = useCallback(() => setLibrary(null), []);
  const handleLibrarySelect = useCallback((project: Project, sessionId: string) => {
    if (isBusy || managingRef.current) return;
    handleSelectProjectSession(project, sessionId);
    closeLibrary();
  }, [isBusy, handleSelectProjectSession, closeLibrary]);

  const handleAddProject = useCallback(async () => {
    if (isBusy || hookIsSwitchingRef.current) return null;
    return addProject();
  }, [isBusy, addProject, hookIsSwitchingRef]);

  const activeSession = sessions.find((s) => s.id === activeSessionId);
  const sessionTitle = activeSession
    ? activeSession.title
    : (sessions.length > 0 ? '选择或新建会话' : '新对话');

  const isComposerDisabled =
    !activeProject ||
    backendStatus?.state !== 'ready' ||
    sessionLoading ||
    isConfirmPending ||
    isSwitchingModel || isManaging || activeArchived;

  const isModelPickerDisabled =
    !activeProject ||
    backendStatus?.state !== 'ready' ||
    isTurnRunning ||
    isConfirmPending ||
    sessionLoading || isManaging;

  const activeError = managementError || bridgeError || sessionError || turnError;

  return (
    <div className="app-container">
      {/* Slim project/session sidebar */}
      <Sidebar
        collapsed={sidebarCollapsed}
        projects={projects}
        activeProject={activeProject}
        onSelectProject={handleSelectProject}
        onAddProject={handleAddProject}
        sessions={sessions.filter(s => !s.archived)}
        history={history}
        historyLoading={historyLoading}
        historyError={historyError}
        onReloadHistory={handleReloadHistory}
        activeSessionId={activeSessionId}
        onSelectSession={handleSelectProjectSession}
        onNewSession={createSession}
        onDeleteSession={handleDeleteSession}
        isTurnRunning={isTurnRunning}
        isConfirmPending={isConfirmPending}
        loading={sessionLoading}
        isSwitchingModel={isSwitchingModel}
        isManaging={isManaging}
        onRemoveProject={handleRemoveProject}
        onArchiveSession={handleArchiveSession}
        onOpenSearch={() => setLibrary('all')}
        onOpenArchive={() => setLibrary('archived')}
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
                setManagementError(null);
              }}
              style={{ color: 'var(--danger)', padding: 4 }}
              title="关闭提示"
            >
              <XIcon size={14} />
            </button>
          </div>
        )}

        {activeArchived && activeProject && activeSessionId && <div className="archived-notice">
          <span>这段对话已归档，取消归档后可继续。</span>
          <button type="button" disabled={isBusy} onClick={() => void handleArchiveSession(activeProject, activeSessionId, false)}>取消归档</button>
        </div>}
        <div className={`conversation-layout ${messages.length === 0 ? 'is-empty' : ''}`}>
          {messages.length === 0 ? (
            <EmptyState project={activeProject} onAddProject={handleAddProject} />
          ) : (
            <ChatArea
              messages={messages}
              clarifyRequest={clarifyRequest}
              onRespondClarify={respondClarify}
            />
          )}

          <Composer
            onSend={handleSend}
            onCancel={cancelTurn}
            isTurnRunning={isTurnRunning}
            disabled={isComposerDisabled}
            initialValue={composerDraft}
            project={activeProject}
            modelPicker={
              <ModelPicker
                key={activeProject?.id}
                currentModel={modelOptions?.model || null}
                currentProvider={modelOptions?.provider || null}
                options={modelOptions}
                loading={modelLoading}
                error={modelError}
                isSwitching={isSwitchingModel}
                switchError={modelSwitchError}
                disabled={isModelPickerDisabled}
                onSelectModel={selectModel}
                onReload={reloadModelOptions}
              />
            }
          />
          {messages.length === 0 && activeProject && (
            <QuickPrompts onSelectPrompt={handleSelectPrompt} disabled={isBusy || isComposerDisabled} />
          )}
        </div>
      </main>

      {/* Real Top-level Confirm Dialog for confirm.request (session deletion) */}
      {library && <ConversationLibrary projects={projects} history={history} initialFilter={library} disabled={isBusy}
        onClose={closeLibrary} onSelect={handleLibrarySelect} onArchive={handleArchiveSession} />}
      {confirmRequest && (
        <ConfirmModal
          request={confirmRequest}
          onRespond={respondConfirm}
        />
      )}
    </div>
  );
}
