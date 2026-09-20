import { useEffect, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  BadgeCheck,
  Eye,
  EyeOff,
  Lock,
  Mail,
  Moon,
  Shield,
  Sun,
  Waypoints,
} from "lucide-react";

import { cn } from "@/lib/cn";
import { useAuth } from "@/state/auth";
import { useTheme } from "@/state/theme";
import { Logo, LogoMark } from "@/components/brand/Logo";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";

/**
 * Sign in / sign up.
 *
 * One component for both, because the forms are nearly identical and keeping them
 * together means the validation, error display and redirect behaviour cannot
 * drift apart.
 *
 * The redirect target comes from `location.state.from`, set by the route guard
 * when it bounced the user here. That is what makes a deep link work: sign in and
 * you land on the page you actually asked for, not the dashboard.
 */

type AuthMode = "sign-in" | "sign-up";

const HIGHLIGHTS = [
  {
    icon: <Waypoints className="h-4 w-4" />,
    title: "See every step",
    body: "Retrieval, context building, generation and grounding, with real timings.",
  },
  {
    icon: <BadgeCheck className="h-4 w-4" />,
    title: "Check every claim",
    body: "Each answer is labelled by how well the evidence actually supports it.",
  },
  {
    icon: <Shield className="h-4 w-4" />,
    title: "Kept separate",
    body: "Documents, vectors and conversations are isolated per workspace.",
  },
];

/** Mirrors the server's rule so the user is told before a round trip. */
function passwordProblems(password: string): string[] {
  const problems: string[] = [];
  if (password.length < 8) problems.push("at least 8 characters");
  if (password.length > 200) problems.push("no more than 200 characters");
  return problems;
}

export default function Auth({ mode }: { mode: AuthMode }) {
  const isSignUp = mode === "sign-up";
  const { login, register, submitting, error, clearError } = useAuth();
  const { resolved, toggle } = useTheme();
  const navigate = useNavigate();
  const location = useLocation();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [localError, setLocalError] = useState("");

  // Clear any leftover auth error when switching between the two forms,
  // otherwise "An account with that email already exists" lingers on the
  // sign-in form where it makes no sense.
  useEffect(() => {
    clearError();
    setLocalError("");
  }, [mode, clearError]);

  const redirectTo = (location.state as { from?: string } | null)?.from ?? "/app";

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setLocalError("");

    const trimmedEmail = email.trim();
    if (!trimmedEmail) {
      setLocalError("Enter your email address.");
      return;
    }

    if (isSignUp) {
      const problems = passwordProblems(password);
      if (problems.length) {
        setLocalError(`Your password needs ${problems.join(" and ")}.`);
        return;
      }
    } else if (!password) {
      setLocalError("Enter your password.");
      return;
    }

    try {
      if (isSignUp) {
        await register(trimmedEmail, password, displayName.trim());
      } else {
        await login(trimmedEmail, password);
      }
      navigate(redirectTo, { replace: true });
    } catch {
      /* the provider already surfaced a message */
    }
  }

  const shownError = localError || error;

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      {/* ================= form side ================= */}
      <div className="flex flex-col bg-canvas px-5 py-6 sm:px-10">
        <div className="flex items-center justify-between">
          <Link to="/" className="rounded-lg">
            <Logo size={26} />
          </Link>

          <Button
            variant="ghost"
            size="icon"
            onClick={toggle}
            aria-label={`Switch to ${resolved === "dark" ? "light" : "dark"} mode`}
          >
            {resolved === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </Button>
        </div>

        <div className="flex flex-1 items-center justify-center py-10">
          <div className="w-full max-w-sm animate-fade-up">
            <h1 className="font-display text-2xl font-bold tracking-tight text-ink">
              {isSignUp ? "Create your account" : "Welcome back"}
            </h1>
            <p className="mt-1.5 text-xs leading-relaxed text-muted">
              {isSignUp
                ? "You will get your own isolated workspace to upload documents into."
                : "Sign in to open your workspaces and conversations."}
            </p>

            <form onSubmit={handleSubmit} className="mt-7 space-y-4" noValidate>
              {isSignUp ? (
                <Input
                  label="Name"
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                  placeholder="Abishek"
                  autoComplete="name"
                  maxLength={120}
                  hint="Optional - shown in the sidebar."
                />
              ) : null}

              <Input
                label="Email"
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="you@college.edu"
                autoComplete="email"
                icon={<Mail className="h-3.5 w-3.5" />}
                required
              />

              <div className="relative">
                <Input
                  label="Password"
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder={isSignUp ? "At least 8 characters" : "Your password"}
                  autoComplete={isSignUp ? "new-password" : "current-password"}
                  icon={<Lock className="h-3.5 w-3.5" />}
                  required
                  className="pr-10"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((value) => !value)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  className="absolute right-2.5 top-[1.85rem] rounded p-1 text-faint transition-colors hover:text-ink"
                >
                  {showPassword ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                </button>
              </div>

              {shownError ? (
                <div
                  role="alert"
                  className="animate-slide-down rounded-lg border border-negative/30 bg-negative/8 px-3 py-2.5"
                >
                  <p className="text-2xs leading-relaxed text-negative">{shownError}</p>
                </div>
              ) : null}

              <Button
                type="submit"
                variant="primary"
                size="lg"
                fullWidth
                loading={submitting}
                className="mt-1"
              >
                {isSignUp ? "Create account" : "Sign in"}
              </Button>
            </form>

            <p className="mt-6 text-center text-2xs text-muted">
              {isSignUp ? "Already have an account?" : "New to Carinaa?"}{" "}
              <Link
                to={isSignUp ? "/sign-in" : "/sign-up"}
                className="font-medium text-brand underline decoration-brand/30 underline-offset-2 hover:decoration-brand"
              >
                {isSignUp ? "Sign in" : "Create one"}
              </Link>
            </p>

            <Link
              to="/"
              className="mt-8 flex items-center justify-center gap-1.5 text-2xs text-faint transition-colors hover:text-muted"
            >
              <ArrowLeft className="h-3 w-3" />
              Back to the overview
            </Link>
          </div>
        </div>
      </div>

      {/* ================= brand side ================= */}
      <div className="relative hidden overflow-hidden border-l border-line bg-surface lg:block">
        <div className="bg-brand-wash absolute inset-0" aria-hidden />
        <div className="bg-dotgrid absolute inset-0 opacity-30" aria-hidden />

        <div className="relative flex h-full flex-col justify-center px-12 py-14">
          <div className="max-w-md">
            <LogoMark size={40} />

            <h2 className="mt-6 font-display text-2xl font-bold leading-snug tracking-tight text-ink">
              A retrieval-augmented system you can actually take apart.
            </h2>

            <p className="mt-4 text-sm leading-relaxed text-muted">
              Carinaa does not hide the interesting parts behind a framework. Parsing,
              chunking, embedding, retrieval, grounding and citation resolution are all
              visible, documented, and demonstrable - which is the point of building one.
            </p>

            <ul className="mt-9 space-y-5">
              {HIGHLIGHTS.map((highlight) => (
                <li key={highlight.title} className="flex items-start gap-3.5">
                  <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-line bg-surface text-brand">
                    {highlight.icon}
                  </span>
                  <div>
                    <p className="text-xs font-semibold text-ink">{highlight.title}</p>
                    <p className="mt-0.5 text-2xs leading-relaxed text-muted">{highlight.body}</p>
                  </div>
                </li>
              ))}
            </ul>

            <div className={cn("mt-10 rounded-xl border border-line bg-surface/70 p-4 backdrop-blur")}>
              <p className="text-2xs leading-relaxed text-muted">
                <span className="font-medium text-ink">A note on honesty:</span> grounding
                tells you how well an answer is supported by the evidence it was given. It
                does not guarantee the answer is correct - the evidence itself could be
                wrong. Carinaa says so on every answer rather than implying more confidence
                than it has.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
