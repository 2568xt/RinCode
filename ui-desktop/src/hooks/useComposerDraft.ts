import { useState } from 'react';

export function useComposerDraft(projectId: string | null, sessionId: string | null) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const key = JSON.stringify([projectId, sessionId]);

  const setDraft = (text: string) => {
    setDrafts(previous => {
      const next = { ...previous };
      if (text) next[key] = text;
      else delete next[key];
      return next;
    });
  };

  return { draft: drafts[key] || '', setDraft, clearDraft: () => setDraft('') };
}
