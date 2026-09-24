"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { MotionIcon } from "motion-icons-react";
import { OpsSidebar } from "@/components/common/OpsSidebar";
import { HoverPeekPanel } from "@/components/common/HoverPeekPanel";
import { AvatarMenu } from "@/components/common/AvatarMenu";
import { UsagePopover } from "@/components/common/UsagePopover";
import { SignInScreen } from "@/components/views/SignInScreen";
import { useAuth } from "@/lib/auth-context";
import { NAV_ITEMS, isActiveHref } from "@/lib/nav-config";
import { useTheme } from "@/components/common/ThemeProvider";
import { cn } from "@/lib/utils";

/**
 * Shell top bar — the ONLY primary navigation surface:
 * [Logo] Home Investigation Pricing History … [Docs?] [theme] [Avatar/Sign In].
 * Chrome uses shell tokens, never pure black/white.
 */
function TopBar({
  onOpenMenu,
  sidebarCollapsed,
  onTogglePanel,
}: {
  onOpenMenu: () => void;
  sidebarCollapsed: boolean;
  onTogglePanel: () => void;
}) {
  const pathname = usePathname();
  const { theme, toggleTheme } = useTheme();
  const { user, loading } = useAuth();

  const link = cn(
    "text-sm font-medium text-ink-muted transition-colors hover:text-ink"
  );

  // Borderless icon buttons — no rectangular boxes around icons. The
  // 8×8 hit area stays; hover is a muted tint.
  const iconButton =
    "flex h-8 w-8 items-center justify-center rounded-full text-ink-muted transition-colors hover:bg-bg-subtle hover:text-ink";

  const navItems = NAV_ITEMS.filter((item) => item.topBar);

  return (
    <header className="flex h-16 shrink-0 items-center justify-between border-b border-border bg-shell-content px-4 sm:px-8">
      <div className="flex min-w-0 items-center gap-6">
        <button
          type="button"
          onClick={onOpenMenu}
          aria-label="Open navigation"
          className={cn(iconButton, "-ml-1 lg:hidden")}
        >
          <MotionIcon name="Menu" size={16} aria-hidden="true" animation="nudge" trigger="hover" />
        </button>

        {/* Fixed sidebar toggle slot (desktop): same screen position whether
            the panel is open or closed — only the icon flips, like the
            reference titlebar toggle. */}
        {user ? (
          <button
            type="button"
            onClick={onTogglePanel}
            aria-label={sidebarCollapsed ? "Expand operations panel" : "Collapse operations panel"}
            aria-expanded={!sidebarCollapsed}
            title={sidebarCollapsed ? "Expand operations panel" : "Collapse operations panel"}
            className={cn(iconButton, "-ml-1 hidden lg:flex")}
          >
            <MotionIcon
              name={sidebarCollapsed ? "PanelLeftOpen" : "PanelLeftClose"}
              size={16}
              aria-hidden="true"
              animation="nudge"
              trigger="hover"
            />
          </button>
        ) : null}

        <Link
          href="/"
          aria-label="PostHarvest home"
          className="shrink-0 font-display text-lg font-semibold tracking-tight text-ink"
        >
          PostHarvest
        </Link>

        <nav aria-label="Primary" className="hidden items-center gap-6 lg:flex">
          {navItems.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              aria-current={isActiveHref(item.href, pathname) ? "page" : undefined}
              className={cn(link, isActiveHref(item.href, pathname) && "text-ink")}
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </div>

      <div className="flex shrink-0 items-center gap-3 sm:gap-5">
        <Link href="/docs" aria-label="Help and docs" title="Help and docs" className={iconButton}>
          <MotionIcon name="BookOpen" size={16} aria-hidden="true" animation="flip" trigger="hover" />
        </Link>
        {user ? <UsagePopover /> : null}
        <button type="button" onClick={toggleTheme} aria-label="Toggle theme" className={iconButton}>
          {theme === "dark" ? (
            <MotionIcon name="Sun" size={16} aria-hidden="true" animation="spin" trigger="hover" />
          ) : (
            <MotionIcon name="Moon" size={16} aria-hidden="true" animation="swing" trigger="hover" />
          )}
        </button>
        {!loading && !user ? (
          <Link
            href="/login"
            className="flex items-center rounded-sm bg-ink px-4 py-2 text-sm font-medium text-bg transition-colors hover:bg-ink/85"
          >
            Sign In
          </Link>
        ) : null}
        {user ? <AvatarMenu /> : null}
      </div>
    </header>
  );
}

/**
 * Root shell — rows, like the reference titlebar: a full-width top bar on
 * top (the toggle slot never moves), then the sidebar + content row below.
 * The top bar owns all primary navigation; the sidebar never renders links.
 */
export default function AppShellLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { user, loading } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    try {
      return window.localStorage.getItem("postharvest.sidebar-collapsed") === "1";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    setMounted(true);
  }, []);

  // Close the mobile drawer whenever the route changes.
  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);

  const toggleSidebar = () => {
    setSidebarCollapsed((current) => {
      const next = !current;
      try {
        window.localStorage.setItem("postharvest.sidebar-collapsed", next ? "1" : "0");
      } catch {
        // Private mode / blocked storage — collapse still works for the session.
      }
      return next;
    });
  };

  // Login gate: the landing home and the docs are public; every other route in
  // the app shell requires a Firebase session. While Firebase restores its
  // session we show a splash so the shell doesn't flash signed-out.
  const isPublicRoute = pathname === "/" || pathname === "/docs" || pathname.startsWith("/docs/");
  if (loading) {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-bg">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-border border-t-accent" />
      </div>
    );
  }
  if (!user && !isPublicRoute) {
    return <SignInScreen />;
  }

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-bg font-sans text-ink antialiased">
      {/* Full-width top row: the toggle slot never moves, so collapsing the
          rail below reflows nothing up here — cursor stays put. */}
      <TopBar
        onOpenMenu={() => setMenuOpen(true)}
        sidebarCollapsed={sidebarCollapsed}
        onTogglePanel={toggleSidebar}
      />

      <div className="relative flex min-h-0 flex-1">
        {/* No sidebar at all for logged-out visitors — the ops panel is
            session chrome, and there is nothing live to show without one. */}
        {user && !sidebarCollapsed ? (
          <aside className="hidden min-h-0 shrink-0 lg:block">
            <OpsSidebar />
          </aside>
        ) : null}

        {/* Hover-to-peek while collapsed (desktop): the edge strip reveals
            the full panel as a usable overlay; leaving dismisses it. */}
        {user && sidebarCollapsed ? <HoverPeekPanel /> : null}

        {/* Mobile slide-over: primary nav (hidden from the top bar below lg).
            Signed-in users also get the ops panel underneath. Route changes
            close it via pathname. */}
        {mounted && menuOpen ? (
          <div className="fixed inset-0 z-50 lg:hidden">
            <button
              type="button"
              aria-label="Close panel"
              className="absolute inset-0 bg-shell-sidebar/60"
              onClick={() => setMenuOpen(false)}
            />
            <div className="absolute inset-y-0 left-0 z-10 flex w-72 animate-slide-in-left flex-col bg-bg-elevated">
              <nav aria-label="Primary" className="flex shrink-0 flex-col border-b border-shell-border px-3 py-3">
                {NAV_ITEMS.filter((item) => item.topBar).map((item) => (
                  <Link
                    key={item.href}
                    href={item.href}
                    aria-current={isActiveHref(item.href, pathname) ? "page" : undefined}
                    className={cn(
                      "border-l-2 px-3 py-2 text-sm font-medium transition-colors",
                      isActiveHref(item.href, pathname)
                        ? "border-shell-accent text-shell-ink"
                        : "border-transparent text-ink-muted",
                    )}
                  >
                    {item.label}
                  </Link>
                ))}
              </nav>
              {user ? (
                <div className="min-h-0 flex-1">
                  <OpsSidebar />
                </div>
              ) : null}
            </div>
          </div>
        ) : null}

        <main className="flex min-w-0 flex-1 flex-col overflow-y-auto bg-shell-content text-shell-fg">
          <div className="mx-auto w-full max-w-[1080px] flex-1">
            <div className="px-4 py-8 sm:px-8">{children}</div>
          </div>
        </main>
      </div>
    </div>
  );
}