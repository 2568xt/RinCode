import React, { useEffect, useRef, useState } from 'react';
import type { Project, SessionListItem } from '../types';
import type { ProjectHistory } from '../hooks/useProjectHistory';
import { XIcon } from '../icons';
import './ConversationLibrary.css';

type Filter = 'all' | 'active' | 'archived';
interface Props {
  projects: Project[];
  history: ProjectHistory;
  initialFilter: Filter;
  disabled: boolean;
  onClose: () => void;
  onSelect: (project: Project, sessionId: string) => void;
  onArchive: (project: Project, sessionId: string, archived: boolean) => Promise<boolean>;
}

export function ConversationLibrary({ projects, history, initialFilter, disabled, onClose, onSelect, onArchive }: Props) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState(initialFilter);
  const [results, setResults] = useState<ProjectHistory>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const panel = useRef<HTMLDivElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const canClose = useRef(!disabled);
  canClose.current = !disabled;

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    search.current?.focus();
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && canClose.current) { event.preventDefault(); onClose(); }
      if (event.key !== 'Tab') return;
      const controls = Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input') || []);
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', keyboard);
    return () => { document.removeEventListener('keydown', keyboard); previous?.focus(); };
  }, [onClose]);

  useEffect(() => {
    let current = true;
    setLoading(true);
    setError(null);
    const timer = window.setTimeout(async () => {
      try {
        const response = await window.rincode.projectHistory({ query: query.trim() });
        if (current) setResults(response);
      } catch {
        if (current) setError('搜索失败，请重试');
      } finally { if (current) setLoading(false); }
    }, query ? 180 : 0);
    return () => { current = false; window.clearTimeout(timer); };
  }, [query, projects, history, retry]);

  const matches = (session: SessionListItem) => filter === 'all' || Boolean(session.archived) === (filter === 'archived');
  const count = projects.reduce((total, project) => total + (results[project.id]?.sessions.filter(matches).length || 0), 0);
  return <div className="library-backdrop" onMouseDown={event => {
    if (event.target === event.currentTarget && !disabled) onClose();
  }}>
    <div ref={panel} className="conversation-library" role="dialog" aria-modal="true" aria-label="查找对话">
      <div className="library-header">
        <div><h2>查找对话</h2><p>搜索所有工作区项目的标题与消息正文</p></div>
        <button type="button" aria-label="关闭对话查找" disabled={disabled} onClick={onClose}><XIcon size={18} /></button>
      </div>
      <input ref={search} className="library-search" aria-label="搜索对话关键词" placeholder="输入关键词，例如：缓存、README…"
        value={query} onChange={event => setQuery(event.target.value)} />
      <div className="library-filter" aria-label="对话范围">
        {([['all', '全部'], ['active', '未归档'], ['archived', '已归档']] as const).map(([value, label]) =>
          <button type="button" key={value} aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</button>)}
        <span>{loading ? '搜索中…' : `${count} 条对话`}</span>
      </div>
      {actionError && <p className="library-action-error" role="alert">{actionError}</p>}
      <div className="library-results" aria-busy={loading}>
        {loading ? <p className="library-empty" role="status">正在查找…</p>
          : error ? <p className="library-empty" role="alert">{error} <button type="button" onClick={() => setRetry(value => value + 1)}>重试</button></p>
          : <>
            {count === 0 && <p className="library-empty">{query.trim() ? '没有找到匹配的对话' : filter === 'archived' ? '还没有归档的对话' : '还没有已保存的对话'}</p>}
            {projects.map(project => {
              const entry = results[project.id];
              const sessions = entry?.sessions.filter(matches) || [];
              if (!sessions.length && !entry?.error) return null;
              return <section key={project.id} className="library-project" data-project-id={project.id}>
                <h3 title={project.path}>{project.name}</h3>
                {entry?.error && <p role="alert">{entry.error}</p>}
                {sessions.map(session => <div className="library-result" key={session.id} data-session-id={session.id}>
                  <button type="button" className="library-open" disabled={disabled} onClick={() => onSelect(project, session.id)}>
                    <span className="library-result-title">{session.title || '新对话'}{session.archived && <small>已归档</small>}</span>
                    {session.preview && <span className="library-snippet">{session.preview}</span>}
                  </button>
                  <button type="button" className="library-archive" disabled={disabled}
                    onClick={async () => {
                      setActionError(null);
                      if (!await onArchive(project, session.id, !session.archived)) setActionError('操作未完成，请重试');
                    }}>{session.archived ? '取消归档' : '归档'}</button>
                </div>)}
              </section>;
            })}
          </>}
      </div>
    </div>
  </div>;
}
