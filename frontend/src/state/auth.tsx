import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { ApiError, api, clearToken, getToken, setToken } from "@/lib/api";
import type { User, UserPreferences } from "@/lib/types";

/**
 * Authentication state.
 *
 * The session is a JWT in localStorage. On mount we verify it against
 * `/auth/me` rather than trusting it: a token can be present but expired, and
 * showing a signed-in shell that 401s on the first request is worse than showing
 * the sign-in screen immediately.
 *
 * Anything that receives a 401 clears the session. That single rule means an
 * expired token can never leave the app in a half-authenticated state.
 */

interface AuthContextValue {
  user: User | null;
  /** True until the initial token check finishes. Gates the app shell. */
  loading: boolean;
  /** True when a sign-in or registration request is in flight. */
  submitting: boolean;
  error: string;
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, displayName: string) => Promise<void>;
  logout: () => Promise<void>;
  updatePreferences: (preferences: UserPreferences) => Promise<void>;
  clearError: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  /* ---- initial session check ---------------------------------------- */
  useEffect(() => {
    let cancelled = false;

    async function bootstrap() {
      if (!getToken()) {
        setLoading(false);
        return;
      }
      try {
        const me = await api.auth.me();
        if (!cancelled) setUser(me);
      } catch (cause) {
        // An expired or revoked token is not an error worth surfacing - it just
        // means there is no session. Anything else (server down) also leaves us
        // signed out, but we do not clear a token that might still be valid.
        if (cause instanceof ApiError && cause.isAuthError) {
          clearToken();
        }
        if (!cancelled) setUser(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    setSubmitting(true);
    setError("");
    try {
      const result = await api.auth.login(email, password);
      setToken(result.access_token);
      setUser(result.user);
    } catch (cause) {
      const message =
        cause instanceof ApiError ? cause.message : "Could not sign in. Please try again.";
      setError(message);
      throw cause;
    } finally {
      setSubmitting(false);
    }
  }, []);

  const register = useCallback(
    async (email: string, password: string, displayName: string) => {
      setSubmitting(true);
      setError("");
      try {
        const result = await api.auth.register(email, password, displayName);
        setToken(result.access_token);
        setUser(result.user);
      } catch (cause) {
        const message =
          cause instanceof ApiError
            ? cause.message
            : "Could not create the account. Please try again.";
        setError(message);
        throw cause;
      } finally {
        setSubmitting(false);
      }
    },
    [],
  );

  const logout = useCallback(async () => {
    // Tell the server, but never let a failure to do so block the sign-out. The
    // token is stateless; clearing it locally is what actually ends the session.
    try {
      await api.auth.logout();
    } catch {
      /* ignore */
    }
    clearToken();
    setUser(null);
  }, []);

  const updatePreferences = useCallback(async (preferences: UserPreferences) => {
    const updated = await api.auth.updatePreferences(preferences);
    setUser(updated);
  }, []);

  const clearError = useCallback(() => setError(""), []);

  const value = useMemo(
    () => ({
      user,
      loading,
      submitting,
      error,
      isAuthenticated: Boolean(user),
      login,
      register,
      logout,
      updatePreferences,
      clearError,
    }),
    [user, loading, submitting, error, login, register, logout, updatePreferences, clearError],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside an AuthProvider.");
  return context;
}
