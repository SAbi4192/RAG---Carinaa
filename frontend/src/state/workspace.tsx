import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/state/auth";
import type { Workspace } from "@/lib/types";

/**
 * Workspace state - the active isolation boundary.
 *
 * A workspace is not a folder. It is the security boundary: retrieval is filtered
 * by `workspace_id` at the vector store, and the server independently verifies
 * ownership. So "which workspace am I in" is a security-relevant question, and
 * the active id is kept here in one place rather than being passed around as a
 * prop or, worse, read from the URL.
 *
 * The selection is persisted so a reload returns the user to the workspace they
 * were working in. It is re-validated on load: a stored id that no longer exists
 * (deleted in another tab, say) falls back to the first available workspace
 * instead of leaving the app pointed at nothing.
 */

const ACTIVE_KEY = "carinaa.workspace";

interface WorkspaceContextValue {
  workspaces: Workspace[];
  activeId: number | null;
  active: Workspace | null;
  loading: boolean;
  error: string;
  select: (id: number) => void;
  refresh: () => Promise<void>;
  create: (name: string, description?: string, color?: string) => Promise<Workspace>;
  update: (id: number, patch: Partial<Pick<Workspace, "name" | "description" | "color">>) => Promise<void>;
  remove: (id: number) => Promise<void>;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

function readStoredId(): number | null {
  try {
    const raw = localStorage.getItem(ACTIVE_KEY);
    if (!raw) return null;
    const parsed = Number.parseInt(raw, 10);
    return Number.isFinite(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const { isAuthenticated, loading: authLoading } = useAuth();

  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [activeId, setActiveId] = useState<number | null>(readStoredId);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const select = useCallback((id: number) => {
    setActiveId(id);
    try {
      localStorage.setItem(ACTIVE_KEY, String(id));
    } catch {
      /* non-fatal */
    }
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await api.workspaces.list();
      setWorkspaces(result.workspaces);

      // Reconcile the selection against what actually exists.
      setActiveId((current) => {
        const stillExists = result.workspaces.some((workspace) => workspace.id === current);
        const next = stillExists ? current : (result.workspaces[0]?.id ?? null);
        if (next !== current) {
          try {
            if (next === null) localStorage.removeItem(ACTIVE_KEY);
            else localStorage.setItem(ACTIVE_KEY, String(next));
          } catch {
            /* non-fatal */
          }
        }
        return next;
      });
    } catch (cause) {
      // A 401 is handled by the auth layer redirecting to sign-in; there is no
      // point also showing "could not load workspaces".
      if (cause instanceof ApiError && cause.isAuthError) return;
      setError(
        cause instanceof ApiError ? cause.message : "Could not load your workspaces.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  // Load once we know there is a session. Waiting for `authLoading` to finish
  // avoids firing a guaranteed-401 request on every cold start.
  useEffect(() => {
    if (authLoading) return;
    if (!isAuthenticated) {
      setWorkspaces([]);
      setActiveId(null);
      return;
    }
    void refresh();
  }, [authLoading, isAuthenticated, refresh]);

  const create = useCallback(
    async (name: string, description = "", color = "violet") => {
      const created = await api.workspaces.create(name, description, color);
      setWorkspaces((current) => [created, ...current]);
      select(created.id);
      return created;
    },
    [select],
  );

  const update = useCallback(
    async (id: number, patch: Partial<Pick<Workspace, "name" | "description" | "color">>) => {
      const updated = await api.workspaces.update(id, patch);
      setWorkspaces((current) =>
        current.map((workspace) => (workspace.id === id ? { ...workspace, ...updated } : workspace)),
      );
    },
    [],
  );

  const remove = useCallback(
    async (id: number) => {
      await api.workspaces.remove(id);
      setWorkspaces((current) => {
        const remaining = current.filter((workspace) => workspace.id !== id);
        setActiveId((active) => {
          if (active !== id) return active;
          const next = remaining[0]?.id ?? null;
          try {
            if (next === null) localStorage.removeItem(ACTIVE_KEY);
            else localStorage.setItem(ACTIVE_KEY, String(next));
          } catch {
            /* non-fatal */
          }
          return next;
        });
        return remaining;
      });
    },
    [],
  );

  const active = useMemo(
    () => workspaces.find((workspace) => workspace.id === activeId) ?? null,
    [workspaces, activeId],
  );

  const value = useMemo(
    () => ({ workspaces, activeId, active, loading, error, select, refresh, create, update, remove }),
    [workspaces, activeId, active, loading, error, select, refresh, create, update, remove],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspaces(): WorkspaceContextValue {
  const context = useContext(WorkspaceContext);
  if (!context) throw new Error("useWorkspaces must be used inside a WorkspaceProvider.");
  return context;
}
