import { useState, useEffect, useCallback, useRef } from 'react';
import type { BackendStatus, ModelOptionsResponse, ModelSelectResponse, Project } from '../types';

interface UseModelsProps {
  activeProject: Project | null;
  backendStatus: BackendStatus | null;
  rpc: (projectId: string, method: string, params?: Record<string, unknown>) => Promise<any>;
  isInteractionBlocked: () => boolean;
}

export function useModels({ activeProject, backendStatus, rpc, isInteractionBlocked }: UseModelsProps) {
  const projectId = activeProject?.id;
  const ready = backendStatus?.state === 'ready';
  const [options, setOptions] = useState<ModelOptionsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isSwitching, setIsSwitching] = useState(false);
  const [switchError, setSwitchError] = useState<string | null>(null);
  const isSwitchingRef = useRef(false);
  const loadingRef = useRef(false);
  const generation = useRef(0);
  const loadRequest = useRef(0);
  const context = useRef({ projectId, ready, isInteractionBlocked });
  context.current = { projectId, ready, isInteractionBlocked };

  const reloadOptions = useCallback(async () => {
    if (!projectId || !ready || context.current.projectId !== projectId || !context.current.ready || isSwitchingRef.current) return;
    const epoch = generation.current;
    const request = ++loadRequest.current;
    const isCurrent = () => generation.current === epoch && loadRequest.current === request &&
      context.current.projectId === projectId && context.current.ready;
    loadingRef.current = true;
    setLoading(true);
    setError(null);
    try {
      const response: ModelOptionsResponse = await rpc(projectId, 'desktop.model.options', {});
      if (isCurrent()) setOptions(response);
    } catch (err) {
      if (isCurrent()) setError(`加载模型列表失败：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      if (isCurrent()) {
        loadingRef.current = false;
        setLoading(false);
      }
    }
  }, [projectId, ready, rpc]);

  // The bridge pairs ready status with its project. Reconnects invalidate pending responses.
  useEffect(() => {
    generation.current++;
    isSwitchingRef.current = false;
    loadingRef.current = false;
    setOptions(null);
    setError(null);
    setSwitchError(null);
    setIsSwitching(false);
    setLoading(false);
    if (projectId && ready) void reloadOptions();
    return () => { generation.current++; };
  }, [projectId, ready, reloadOptions]);

  const selectModel = useCallback(async (provider: string, model: string): Promise<boolean> => {
    if (!projectId || !ready || context.current.projectId !== projectId || !context.current.ready ||
      context.current.isInteractionBlocked() || isSwitchingRef.current || loadingRef.current) return false;
    const epoch = generation.current;
    const isCurrent = () => generation.current === epoch && context.current.projectId === projectId && context.current.ready;
    isSwitchingRef.current = true;
    setIsSwitching(true);
    setSwitchError(null);
    try {
      const response: ModelSelectResponse = await rpc(projectId, 'desktop.model.select', { model, provider });
      if (!isCurrent()) return false;
      if (!response.applied) throw new Error('模型切换未生效');
      setOptions((previous) => previous ? { ...previous, model: response.model, provider: response.provider } : previous);
      return true;
    } catch (err) {
      if (isCurrent()) setSwitchError(`切换模型失败：${err instanceof Error ? err.message : String(err)}`);
      return false;
    } finally {
      if (isCurrent()) {
        isSwitchingRef.current = false;
        setIsSwitching(false);
      }
    }
  }, [projectId, ready, rpc]);

  return { options, loading, error, isSwitching, switchError, isSwitchingRef, reloadOptions, selectModel };
}
