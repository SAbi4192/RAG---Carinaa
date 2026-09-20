import { Link, useLocation, useNavigate } from "react-router-dom";
import { ArrowLeft, Compass, Search } from "lucide-react";

import { useAuth } from "@/state/auth";
import { LogoMark } from "@/components/brand/Logo";
import { Button } from "@/components/ui/Button";

/**
 * 404.
 *
 * Kept in the product's visual language rather than being a bare error page,
 * because a wrong URL is usually a typo or a stale link - the user needs a way
 * forward, not an apology.
 */
export default function NotFound() {
  const { isAuthenticated } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  return (
    <div className="relative flex min-h-screen flex-col items-center justify-center overflow-hidden bg-canvas px-5">
      <div className="bg-brand-wash absolute inset-0" aria-hidden />
      <div className="bg-dotgrid absolute inset-0 opacity-30" aria-hidden />

      <div className="relative w-full max-w-md text-center">
        <LogoMark size={44} className="mx-auto" />

        <p className="mt-6 font-mono text-2xs uppercase tracking-widest text-faint">
          404 — not found
        </p>

        <h1 className="mt-3 font-display text-2xl font-bold tracking-tight text-ink">
          That page does not exist
        </h1>

        <p className="mt-3 text-xs leading-relaxed text-muted">
          Nothing is served at{" "}
          <code className="rounded border border-line bg-sunken px-1.5 py-0.5 font-mono text-2xs text-ink">
            {location.pathname}
          </code>
          . It may have been a typo, or a link to something that has since been removed.
        </p>

        <div className="mt-7 flex flex-wrap items-center justify-center gap-2">
          <Button
            variant="secondary"
            size="md"
            icon={<ArrowLeft className="h-3.5 w-3.5" />}
            onClick={() => navigate(-1)}
          >
            Go back
          </Button>

          <Link to={isAuthenticated ? "/app" : "/"}>
            <Button variant="primary" size="md" icon={<Compass className="h-3.5 w-3.5" />}>
              {isAuthenticated ? "Go to the dashboard" : "Go to the homepage"}
            </Button>
          </Link>
        </div>

        {isAuthenticated ? (
          <div className="mt-8 flex flex-wrap items-center justify-center gap-3 border-t border-line pt-6">
            <span className="text-2xs text-faint">Looking for something specific?</span>
            <Link
              to="/app/knowledge"
              className="flex items-center gap-1 text-2xs font-medium text-brand transition-colors hover:underline"
            >
              <Search className="h-2.5 w-2.5" />
              Knowledge Base
            </Link>
            <Link
              to="/app/chat"
              className="text-2xs font-medium text-brand transition-colors hover:underline"
            >
              Chat
            </Link>
            <Link
              to="/app/settings"
              className="text-2xs font-medium text-brand transition-colors hover:underline"
            >
              Settings
            </Link>
          </div>
        ) : null}
      </div>
    </div>
  );
}
