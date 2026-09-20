import { Suspense, lazy } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { AppShell } from "@/components/layout/AppShell";
import { LoadingPanel } from "@/components/ui/Feedback";
import { LogoMark } from "@/components/brand/Logo";
import { useAuth } from "@/state/auth";

import Landing from "@/pages/Landing";
import Auth from "@/pages/Auth";

/**
 * Routing.
 *
 * The in-app screens are lazy-loaded. The landing page is not: it is the first
 * thing a visitor sees, and making them wait for a second round trip to see the
 * hero would be a poor first impression. Everything behind the sign-in wall is
 * fetched on demand, which keeps the initial bundle small.
 *
 * `/app/*` is wrapped in a guard that also handles the "still checking my token"
 * state. Rendering the shell before that check finishes would flash the app and
 * then bounce the user to the sign-in screen, which reads as a bug.
 */

const Dashboard = lazy(() => import("@/pages/Dashboard"));
const KnowledgeBase = lazy(() => import("@/pages/KnowledgeBase"));
const DocumentViewer = lazy(() => import("@/pages/DocumentViewer"));
const Chat = lazy(() => import("@/pages/Chat"));
const RagTrace = lazy(() => import("@/pages/RagTrace"));
const Playground = lazy(() => import("@/pages/Playground"));
const Analytics = lazy(() => import("@/pages/Analytics"));
const Settings = lazy(() => import("@/pages/Settings"));
const NotFound = lazy(() => import("@/pages/NotFound"));

/** Full-page splash shown while the initial session check runs. */
function BootSplash() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-canvas">
      <LogoMark size={40} className="animate-scale-in" />
      <p className="text-xs text-muted">Checking your session…</p>
    </div>
  );
}

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, loading } = useAuth();
  const location = useLocation();

  if (loading) return <BootSplash />;

  if (!isAuthenticated) {
    // Remember where they were headed so sign-in can return them there.
    return <Navigate to="/sign-in" state={{ from: location.pathname }} replace />;
  }

  return <AppShell>{children}</AppShell>;
}

export default function App() {
  return (
    <Suspense fallback={<LoadingPanel message="Loading…" className="min-h-screen" />}>
      <Routes>
        {/* ---- public ------------------------------------------------ */}
        <Route path="/" element={<Landing />} />
        <Route path="/sign-in" element={<Auth mode="sign-in" />} />
        <Route path="/sign-up" element={<Auth mode="sign-up" />} />

        {/* ---- application ------------------------------------------- */}
        <Route
          path="/app"
          element={
            <RequireAuth>
              <Dashboard />
            </RequireAuth>
          }
        />
        <Route
          path="/app/knowledge"
          element={
            <RequireAuth>
              <KnowledgeBase />
            </RequireAuth>
          }
        />
        <Route
          path="/app/knowledge/:documentId"
          element={
            <RequireAuth>
              <DocumentViewer />
            </RequireAuth>
          }
        />
        <Route
          path="/app/chat"
          element={
            <RequireAuth>
              <Chat />
            </RequireAuth>
          }
        />
        <Route
          path="/app/chat/:conversationId"
          element={
            <RequireAuth>
              <Chat />
            </RequireAuth>
          }
        />
        <Route
          path="/app/trace"
          element={
            <RequireAuth>
              <RagTrace />
            </RequireAuth>
          }
        />
        <Route
          path="/app/trace/:messageId"
          element={
            <RequireAuth>
              <RagTrace />
            </RequireAuth>
          }
        />
        <Route
          path="/app/playground"
          element={
            <RequireAuth>
              <Playground />
            </RequireAuth>
          }
        />
        {/* One bench per pipeline stage. Deep-linkable, so a lab can be shared
            or opened directly during a demonstration. */}
        <Route
          path="/app/playground/:lab"
          element={
            <RequireAuth>
              <Playground />
            </RequireAuth>
          }
        />
        {/*
          Learning Mode is a REAL route, not a redirect and not a query flag.

          It used to redirect to /app/chat?learning=1 and rely on Chat reading the
          flag in a useState initializer. Both failed in practice:
            - a query flag is never re-read, because React Router keeps Chat mounted
              across a search change, so clicking the nav item while already on Chat
              did nothing;
            - /app/learning redirecting away meant the URL the user clicked was not
              the URL they landed on, which reads as "it bounced back to Chat".

          Both routes render the same Chat component with `learning`, so the
          conversation, the answers and the pipeline are identical - only the layout
          differs.
        */}
        <Route
          path="/app/learning"
          element={
            <RequireAuth>
              <Chat learning />
            </RequireAuth>
          }
        />
        <Route
          path="/app/learning/:conversationId"
          element={
            <RequireAuth>
              <Chat learning />
            </RequireAuth>
          }
        />
        <Route
          path="/app/analytics"
          element={
            <RequireAuth>
              <Analytics />
            </RequireAuth>
          }
        />
        <Route
          path="/app/settings"
          element={
            <RequireAuth>
              <Settings />
            </RequireAuth>
          }
        />

        {/* ---- fallback ---------------------------------------------- */}
        <Route path="*" element={<NotFound />} />
      </Routes>
    </Suspense>
  );
}
