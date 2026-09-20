import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import {
  Activity,
  BarChart3,
  BookOpen,
  ChevronDown,
  Database,
  FlaskConical,
  GraduationCap,
  LayoutDashboard,
  Menu,
  MessagesSquare,
  Moon,
  Plus,
  Settings as SettingsIcon,
  Sun,
  Waypoints,
  X,
} from "lucide-react";

import { cn } from "@/lib/cn";
import { useIsCompact } from "@/hooks/useMediaQuery";
import { useAuth } from "@/state/auth";
import { useTheme } from "@/state/theme";
import { useWorkspaces } from "@/state/workspace";
import { Logo, LogoMark } from "@/components/brand/Logo";
import { Button } from "@/components/ui/Button";
import { StatusDot } from "@/components/ui/Badge";
import { WorkspaceSwitcher } from "@/components/layout/WorkspaceSwitcher";

/**
 * The application shell.
 *
 * WHY THE NAV IS GROUPED
 * ----------------------
 * Ten flat links is a wall. The three groups below follow the actual mental
 * model of the product:
 *
 *   WORK       what you do with your documents (read, browse, ask)
 *   UNDERSTAND how the system reached its answer (the educational core)
 *   SYSTEM     how it is configured and measured
 *
 * "Understand" is not filler - it is the differentiator. Putting Trace, Playground
 * and Learning Mode in their own group signals that explainability is a first-class
 * feature rather than a debug screen.
 */

interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  /** Match nested routes too, e.g. /app/knowledge/12 highlights Knowledge Base. */
  match?: string;
  /**
   * Set when the entry is an action rather than a location - a link that takes you
   * somewhere, which should never be highlighted as "where you are". Currently
   * unused: Learning Mode is a real route of its own, so it highlights normally.
   */
  shortcut?: boolean;
}

const NAV_GROUPS: { heading: string; items: NavItem[] }[] = [
  {
    heading: "Work",
    items: [
      { to: "/app", label: "Dashboard", icon: <LayoutDashboard className="h-4 w-4" /> },
      {
        to: "/app/knowledge",
        label: "Knowledge Base",
        icon: <Database className="h-4 w-4" />,
        match: "/app/knowledge",
      },
      {
        to: "/app/chat",
        label: "Chat",
        icon: <MessagesSquare className="h-4 w-4" />,
        match: "/app/chat",
      },
    ],
  },
  {
    heading: "Understand",
    items: [
      {
        to: "/app/trace",
        label: "RAG Trace",
        icon: <Waypoints className="h-4 w-4" />,
        match: "/app/trace",
      },
      {
        to: "/app/playground",
        label: "RAG Laboratory",
        icon: <FlaskConical className="h-4 w-4" />,
        match: "/app/playground",
      },
      {
        to: "/app/learning",
        label: "Learning Mode",
        icon: <GraduationCap className="h-4 w-4" />,
        match: "/app/learning",
      },
    ],
  },
  {
    heading: "System",
    items: [
      {
        to: "/app/analytics",
        label: "Analytics",
        icon: <BarChart3 className="h-4 w-4" />,
        match: "/app/analytics",
      },
      {
        to: "/app/settings",
        label: "Settings",
        icon: <SettingsIcon className="h-4 w-4" />,
        match: "/app/settings",
      },
    ],
  },
];

function SidebarContent({
  onNavigate,
  collapsed = false,
  onToggleCollapsed,
}: {
  onNavigate?: () => void;
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
}) {
  const { resolved, toggle } = useTheme();
  const { user, logout } = useAuth();
  const { active } = useWorkspaces();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);

  const isActive = (item: NavItem) => {
    // A shortcut is never "the page you are on" - it is a way to get somewhere.
    if (item.shortcut) return false;
    return item.match
      ? location.pathname.startsWith(item.match)
      : location.pathname === item.to;
  };

  return (
    <div
      className={cn(
        "flex h-full flex-col gap-1 overflow-y-auto scrollbar-thin py-4",
        collapsed ? "px-2" : "px-3",
      )}
    >
      {/* ☰ lives on the sidebar it controls. In the collapsed state it is the
          only way back, so it must never be hidden. */}
      <div
        className={cn(
          "flex items-center pb-4",
          collapsed ? "justify-center" : "justify-between px-1.5",
        )}
      >
        {collapsed ? (
          <button
            type="button"
            onClick={onToggleCollapsed}
            aria-label="Expand the navigation sidebar"
            aria-expanded="false"
            title="Expand the navigation sidebar"
            className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-muted transition hover:bg-sunken hover:text-ink"
          >
            <Menu className="h-4 w-4" />
          </button>
        ) : (
          <>
            <Logo />
            <button
              type="button"
              onClick={onToggleCollapsed}
              aria-label="Collapse the navigation sidebar"
              aria-expanded="true"
              title="Collapse the navigation sidebar"
              className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-muted transition hover:bg-sunken hover:text-ink"
            >
              <Menu className="h-4 w-4" />
            </button>
          </>
        )}
      </div>

      <WorkspaceSwitcher onNavigate={onNavigate} compact={collapsed} />

      <nav className={cn("flex flex-1 flex-col", collapsed ? "mt-3 gap-3" : "mt-4 gap-5")}>
        {NAV_GROUPS.map((group) => (
          <div key={group.heading}>
            {collapsed ? (
              <div className="mx-2 mb-1.5 border-t border-line" aria-hidden />
            ) : (
              <p className="px-2.5 pb-1.5 text-2xs font-semibold uppercase tracking-wider text-faint">
                {group.heading}
              </p>
            )}
            <ul className="space-y-0.5">
              {group.items.map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    end={!item.match}
                    onClick={onNavigate}
                    title={collapsed ? item.label : undefined}
                    className={cn(
                      "group relative flex items-center rounded-lg py-2 text-xs font-medium transition-all duration-150",
                      collapsed ? "justify-center px-0" : "gap-2.5 px-2.5",
                      isActive(item)
                        ? "bg-brand/10 text-brand"
                        : "text-muted hover:bg-sunken hover:text-ink",
                    )}
                  >
                    {isActive(item) ? (
                      <span className="absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-r-full bg-brand" />
                    ) : null}
                    <span
                      className={cn(
                        isActive(item) ? "text-brand" : "text-faint group-hover:text-muted",
                      )}
                    >
                      {item.icon}
                    </span>
                    {collapsed ? <span className="sr-only">{item.label}</span> : item.label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>

      {/* Footer: active workspace state, theme toggle, account */}
      <div className={cn("mt-4 space-y-2 border-t border-line pt-3", collapsed && "px-0")}>
        {active ? (
          collapsed ? (
            <div
              className="flex justify-center py-1"
              title={`${active.name}${active.stats ? ` · ${active.stats.chunks} chunks indexed` : ""}`}
            >
              <StatusDot tone="positive" />
              <span className="sr-only">{active.name}</span>
            </div>
          ) : (
            <div className="flex items-center gap-2 rounded-lg bg-sunken px-2.5 py-2">
              <StatusDot tone="positive" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-2xs font-medium text-ink">{active.name}</p>
                <p className="text-2xs text-faint">
                  {active.stats ? `${active.stats.chunks} chunks indexed` : "no documents yet"}
                </p>
              </div>
            </div>
          )
        ) : null}

        <button
          type="button"
          onClick={toggle}
          title={collapsed ? (resolved === "dark" ? "Light mode" : "Dark mode") : undefined}
          className={cn(
            "flex w-full items-center rounded-lg py-2 text-xs font-medium text-muted transition-colors hover:bg-sunken hover:text-ink",
            collapsed ? "justify-center px-0" : "gap-2.5 px-2.5",
          )}
        >
          {resolved === "dark" ? (
            <Sun className="h-4 w-4 text-faint" />
          ) : (
            <Moon className="h-4 w-4 text-faint" />
          )}
          {collapsed ? (
            <span className="sr-only">{resolved === "dark" ? "Light mode" : "Dark mode"}</span>
          ) : (
            (resolved === "dark" ? "Light mode" : "Dark mode")
          )}
        </button>

        <div className="relative">
          <button
            type="button"
            onClick={() => setMenuOpen((open) => !open)}
            aria-expanded={menuOpen}
            title={collapsed ? user?.display_name || user?.email || "Account" : undefined}
            className={cn(
              "flex w-full items-center rounded-lg py-2 text-left transition-colors hover:bg-sunken",
              collapsed ? "justify-center px-0" : "gap-2.5 px-2.5",
            )}
          >
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand text-2xs font-semibold text-brand-ink">
              {(user?.display_name || user?.email || "?").slice(0, 1).toUpperCase()}
            </span>
            {collapsed ? null : (
              <>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-2xs font-medium text-ink">
                    {user?.display_name || "Signed in"}
                  </span>
                  <span className="block truncate text-2xs text-faint">{user?.email}</span>
                </span>
                <ChevronDown
                  className={cn(
                    "h-3.5 w-3.5 shrink-0 text-faint transition-transform",
                    menuOpen && "rotate-180",
                  )}
                />
              </>
            )}
          </button>

          {menuOpen ? (
            <div className={cn(
                "absolute bottom-full left-0 mb-1.5 animate-slide-down rounded-lg border border-line bg-raised p-1 shadow-pop",
                collapsed ? "w-52" : "right-0",
              )}>
              <button
                type="button"
                onClick={() => {
                  setMenuOpen(false);
                  onNavigate?.();
                  void logout();
                }}
                className="w-full rounded-md px-2.5 py-2 text-left text-2xs font-medium text-negative transition-colors hover:bg-negative/10"
              >
                Sign out
              </button>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const isCompact = useIsCompact();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const location = useLocation();

  /**
   * Collapsed navigation.
   *
   * Persisted, because collapsing the sidebar is a standing preference, not a
   * one-off. The collapsed rail keeps icons with tooltips and the ☰ that brings
   * it back - it never disappears entirely, or the only way to restore it would
   * be something the user has to remember.
   */
  const [navCollapsed, setNavCollapsed] = useState(
    () => window.localStorage.getItem("carinaa.navCollapsed") === "1",
  );

  useEffect(() => {
    window.localStorage.setItem("carinaa.navCollapsed", navCollapsed ? "1" : "0");
  }, [navCollapsed]);

  // Close the mobile drawer whenever the route changes, otherwise it stays open
  // over the page the user just navigated to.
  useEffect(() => {
    setDrawerOpen(false);
  }, [location.pathname]);

  // Lock body scroll while the drawer is open.
  useEffect(() => {
    if (!isCompact || !drawerOpen) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [isCompact, drawerOpen]);

  // Escape closes the drawer.
  useEffect(() => {
    if (!drawerOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDrawerOpen(false);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [drawerOpen]);

  return (
    <div className="flex min-h-screen bg-canvas">
      {/* ---- desktop rail -------------------------------------------- */}
      {!isCompact ? (
        <aside
          className={cn(
            "sticky top-0 h-screen shrink-0 border-r border-line bg-surface transition-[width] duration-200",
            navCollapsed ? "w-14" : "w-60",
          )}
        >
          <SidebarContent
            collapsed={navCollapsed}
            onToggleCollapsed={() => setNavCollapsed((value) => !value)}
          />
        </aside>
      ) : null}

      {/* ---- mobile drawer ------------------------------------------- */}
      {isCompact && drawerOpen ? (
        <div className="fixed inset-0 z-[80]">
          <div
            className="absolute inset-0 animate-fade-in bg-black/45"
            onClick={() => setDrawerOpen(false)}
            aria-hidden
          />
          <aside className="absolute left-0 top-0 h-full w-64 animate-fade-in border-r border-line bg-surface shadow-pop">
            <SidebarContent onNavigate={() => setDrawerOpen(false)} />
          </aside>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        {/* ---- mobile top bar ---------------------------------------- */}
        {isCompact ? (
          <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-line bg-surface/95 px-4 py-2.5 backdrop-blur">
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setDrawerOpen((open) => !open)}
              aria-label={drawerOpen ? "Close navigation" : "Open navigation"}
              aria-expanded={drawerOpen}
            >
              {drawerOpen ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}
            </Button>
            <LogoMark size={22} />
            <span className="font-display text-sm font-semibold text-ink">Carinaa</span>
          </header>
        ) : null}

        <main className="min-w-0 flex-1">{children}</main>
      </div>
    </div>
  );
}

/**
 * Standard page header. Every screen uses this so titles, descriptions and
 * actions always sit in the same place.
 */
export function PageHeader({
  title,
  description,
  actions,
  icon,
  badge,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  icon?: ReactNode;
  badge?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 border-b border-line bg-surface/60 px-5 py-5 sm:flex-row sm:items-start sm:justify-between sm:px-7">
      <div className="flex min-w-0 items-start gap-3">
        {icon ? (
          <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-line bg-sunken text-brand">
            {icon}
          </div>
        ) : null}
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="font-display text-lg font-semibold tracking-tight text-ink">{title}</h1>
            {badge}
          </div>
          {description ? (
            <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted">{description}</p>
          ) : null}
        </div>
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

/** Page body wrapper with consistent padding and max width. */
export function PageBody({
  children,
  className,
  wide,
}: {
  children: ReactNode;
  className?: string;
  wide?: boolean;
}) {
  return (
    <div
      className={cn(
        "mx-auto w-full px-5 py-6 sm:px-7",
        wide ? "max-w-[1600px]" : "max-w-6xl",
        className,
      )}
    >
      {children}
    </div>
  );
}

/** A small "no workspace yet" gate, so screens can bail out consistently. */
export function NoWorkspaceNotice({ onCreate }: { onCreate?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-line-strong bg-surface/50 px-6 py-12 text-center">
      <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-xl border border-line bg-sunken text-faint">
        <Database className="h-5 w-5" />
      </div>
      <h3 className="text-sm font-semibold text-ink">No workspace selected</h3>
      <p className="mt-1.5 max-w-md text-xs leading-relaxed text-muted">
        A workspace is your isolated knowledge base - documents, vectors and
        conversations never cross between them. Create one to get started.
      </p>
      {onCreate ? (
        <Button variant="primary" size="sm" className="mt-4" icon={<Plus className="h-3.5 w-3.5" />} onClick={onCreate}>
          Create a workspace
        </Button>
      ) : null}
    </div>
  );
}

export { Activity, BookOpen };
