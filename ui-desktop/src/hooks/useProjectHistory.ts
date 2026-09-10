import { useCallback, useEffect, useRef, useState } from 'react';
import type { Project, SessionListItem } from '../types';

export type ProjectHistory = Record<string, { sessions: SessionListItem[]; error?: string }>;

export function useProjectHistory(projects: Project[]) {
  const [history, setHistory] = useState<ProjectHistory>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const request = useRef(0);

  const reload = useCallback(async () => {
    const token = ++request.current;
    setLoading(true);
    setError(null);
    try {
      const result = await window.rincode.projectHistory();
      if (token === request.current) setHistory(result);
    } catch {
      if (token === request.current) setError('历史对话加载失败，请重试');
    } finally {
      if (token === request.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (projects.length) void reload();
    else { setHistory({}); setLoading(false); setError(null); }
    return () => { request.current++; };
  }, [projects, reload]);

  return { history, loading, error, reload };
}
