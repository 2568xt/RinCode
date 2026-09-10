import { useState, useEffect, useCallback, useRef } from 'react';
import type { BackendStatus, DesktopBridge, DesktopEvent, Project } from '../types';

export function useDesktopBridge() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeProject, setActiveProject] = useState<Project | null>(null);
  const [backendStatus, setBackendStatus] = useState<BackendStatus | null>(null);
  const [bridgeReady, setBridgeReady] = useState<boolean>(false);
  const [bridgeError, setBridgeError] = useState<string | null>(null);

  const activeProjectRef = useRef<Project | null>(activeProject);
  activeProjectRef.current = activeProject;

  // Event listener registry to prevent duplicate listeners
  const listenersRef = useRef<Set<(event: DesktopEvent) => void>>(new Set());

  // Check bridge availability
  const getBridge = useCallback((): DesktopBridge | null => {
    if (typeof window !== 'undefined' && window.rincode) {
      return window.rincode;
    }
    return null;
  }, []);

  const addEventListener = useCallback((fn: (event: DesktopEvent) => void) => {
    listenersRef.current.add(fn);
    return () => {
      listenersRef.current.delete(fn);
    };
  }, []);

  // Set up single global event dispatcher from window.rincode.onEvent
  useEffect(() => {
    const bridge = getBridge();
    if (!bridge) {
      setBridgeReady(false);
      return;
    }
    setBridgeReady(true);

    const unsubscribe = bridge.onEvent((event: DesktopEvent) => {
      // Filter backend.status by active project
      if (event.method === 'backend.status') {
        if (!activeProjectRef.current || event.projectId === activeProjectRef.current.id) {
          setBackendStatus(event.params as BackendStatus);
        }
      }
      // Broadcast to registered listeners
      listenersRef.current.forEach((fn) => {
        try {
          fn(event);
        } catch (err) {
          console.error('Error in desktop event listener:', err);
        }
      });
    });

    return () => {
      if (typeof unsubscribe === 'function') {
        unsubscribe();
      }
    };
  }, [getBridge]);

  // Load projects initially
  const reloadProjects = useCallback(async () => {
    const bridge = getBridge();
    if (!bridge) return;
    try {
      setBridgeError(null);
      const list = await bridge.projects();
      setProjects(list || []);
      setActiveProject((prev) => {
        if (prev && list.some((p) => p.id === prev.id)) {
          return prev;
        }
        return list && list.length > 0 ? list[0] : null;
      });
    } catch (err: any) {
      const msg = err?.message || String(err);
      console.error('Failed to load projects:', err);
      setBridgeError(`加载项目列表失败: ${msg}`);
    }
  }, [getBridge]);

  useEffect(() => {
    if (bridgeReady) {
      reloadProjects();
    }
  }, [bridgeReady, reloadProjects]);

  // Connect active project when activeProject changes
  useEffect(() => {
    const bridge = getBridge();
    if (!bridge || !activeProject) {
      setBackendStatus({ state: 'stopped' });
      return;
    }

    let isCurrent = true;
    setBackendStatus({ state: 'starting' });
    setBridgeError(null);

    bridge
      .connect(activeProject.id)
      .then((status) => {
        if (isCurrent && status) {
          setBackendStatus(status);
        }
      })
      .catch((err: any) => {
        if (isCurrent) {
          const msg = err?.message || String(err);
          setBackendStatus({
            state: 'error',
            message: msg,
          });
          setBridgeError(`项目连接失败: ${msg}`);
        }
      });

    return () => {
      isCurrent = false;
    };
  }, [activeProject, getBridge]);

  // Add project handler (native dialog)
  const addProject = useCallback(async () => {
    const bridge = getBridge();
    if (!bridge) return null;
    try {
      setBridgeError(null);
      const newProj = await bridge.addProject();
      if (newProj) {
        await reloadProjects();
        setActiveProject(newProj);
        return newProj;
      }
    } catch (err: any) {
      const msg = err?.message || String(err);
      console.error('Failed to add project:', err);
      setBridgeError(`添加项目失败: ${msg}`);
    }
    return null;
  }, [getBridge, reloadProjects]);

  // Safe RPC caller
  const rpc = useCallback(
    async (projectId: string, method: string, params: Record<string, unknown> = {}) => {
      const bridge = getBridge();
      if (!bridge) {
        throw new Error('DesktopBridge window.rincode not available');
      }
      return bridge.rpc(projectId, method, params);
    },
    [getBridge]
  );

  const clearBridgeError = useCallback(() => setBridgeError(null), []);

  return {
    projects,
    activeProject,
    setActiveProject,
    backendStatus,
    bridgeReady,
    bridgeError,
    clearBridgeError,
    addProject,
    rpc,
    addEventListener,
  };
}
