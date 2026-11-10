"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { toast } from "sonner";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ListChecks, LogOut, Menu, PanelLeftClose, PanelLeftOpen, ScanSearch, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { ConfirmDialog } from "@/components/review/confirm-dialog";
import { StatusBar } from "@/components/scrape/status-bar";
import { announceSessionChange, meQuery, post, statusQuery } from "@/lib/api";
import { cn } from "@/lib/utils";

// Nav stays plain data; admin-only items are hidden for members (pages and the API check again).
const NAV = [
  { href: "/", label: "Review", icon: ListChecks, admin: false },
  { href: "/scraping", label: "Scraping", icon: ScanSearch, admin: false },
  { href: "/accounts", label: "Accounts", icon: Users, admin: true },
] as const;

const COLLAPSE_KEY = "scraper.sidebar.collapsed";
const COLLAPSE_EVENT = "scraper:sidebar";
const subscribeCollapse = (cb: () => void) => {
  window.addEventListener(COLLAPSE_EVENT, cb);
  window.addEventListener("storage", cb);
  return () => { window.removeEventListener(COLLAPSE_EVENT, cb); window.removeEventListener("storage", cb); };
};
const readCollapsed = () => { try { return localStorage.getItem(COLLAPSE_KEY) === "1"; } catch { return false; } };
const isActive = (pathname: string, href: string) =>
  href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(href + "/");

function SidebarContent({ collapsed, onNavigate, onLogout, onToggle }: {
  collapsed: boolean; onNavigate?: () => void; onLogout: () => void; onToggle?: () => void;
}) {
  const pathname = usePathname();
  const { data: me } = useQuery(meQuery);
  const { data: status } = useQuery(statusQuery);
  const pending = status?.candidates.pending;
  const items = NAV.filter((n) => !n.admin || me?.role === "admin");
  const row = "flex h-10 items-center gap-3 rounded-lg px-2.5 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-ring";
  return (
    <div className="flex h-full flex-col">
      <div className={cn("flex h-14 items-center gap-2 border-b px-3", collapsed && "justify-center px-0")}>
        <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <ScanSearch className="h-4 w-4 shrink-0" />
        </div>
        {!collapsed && <span className="truncate text-sm font-semibold tracking-tight">Workspace Scraper</span>}
      </div>
      <nav aria-label="Main" className="flex flex-1 flex-col gap-1 p-2">
        {items.map(({ href, label, icon: Icon }) => {
          const active = isActive(pathname, href);
          const count = href === "/" ? pending : undefined;
          return (
            <Link key={href} href={href} onClick={onNavigate} title={collapsed ? label : undefined} aria-label={label}
              aria-current={active ? "page" : undefined}
              className={cn(row, collapsed && "justify-center px-0",
                active ? "bg-accent text-primary" : "text-muted-foreground hover:bg-accent/60 hover:text-foreground")}>
              <span className="relative">
                <Icon className="h-4 w-4 shrink-0" />
                {collapsed && !!count && <span className="absolute -top-1.5 -right-1.5 h-2 w-2 rounded-full bg-primary" />}
              </span>
              {!collapsed && <span className="truncate">{label}</span>}
              {!collapsed && !!count && (
                <span className="ml-auto rounded-full bg-primary/10 px-1.5 text-xs text-primary tabular-nums">{count > 99 ? "99+" : count}</span>
              )}
            </Link>
          );
        })}
      </nav>
      <div className="flex flex-col gap-1 border-t p-2">
        {me && (
          <div className={cn("flex items-center gap-2 px-1.5 py-1", collapsed && "justify-center px-0")} title={collapsed ? `${me.name} (${me.role})` : undefined}>
            <span aria-hidden className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold uppercase">{me.name.slice(0, 1)}</span>
            {!collapsed && (
              <span className="min-w-0 leading-tight">
                <span className="block truncate text-sm font-medium">{me.name}</span>
                <span className="block truncate text-xs text-muted-foreground capitalize">{me.role}</span>
              </span>
            )}
          </div>
        )}
        <button type="button" onClick={onLogout} aria-label="Sign out" title={collapsed ? "Sign out" : undefined}
          className={cn(row, "text-destructive hover:bg-destructive/10", collapsed && "justify-center px-0")}>
          <LogOut className="h-4 w-4 shrink-0" />{!collapsed && "Sign out"}
        </button>
        {onToggle && (
          <button type="button" onClick={onToggle} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={collapsed ? "Expand sidebar" : undefined}
            className={cn(row, "text-muted-foreground hover:bg-accent/60 hover:text-foreground", collapsed && "justify-center px-0")}>
            {collapsed ? <PanelLeftOpen className="h-4 w-4 shrink-0" /> : <><PanelLeftClose className="h-4 w-4 shrink-0" /> Collapse</>}
          </button>
        )}
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  // Server snapshot is "expanded"; the saved choice applies right after hydration.
  const collapsed = useSyncExternalStore(subscribeCollapse, readCollapsed, () => false);
  const [drawer, setDrawer] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  const toggle = () => {
    try { localStorage.setItem(COLLAPSE_KEY, collapsed ? "0" : "1"); } catch { /* storage unavailable */ }
    window.dispatchEvent(new Event(COLLAPSE_EVENT));
  };

  // Another tab signed in or out: reload so this one never shows the previous account's cached data.
  useEffect(() => {
    let ch: BroadcastChannel;
    try { ch = new BroadcastChannel("scraper-session"); } catch { return; }
    ch.onmessage = () => window.location.reload();
    return () => ch.close();
  }, []);

  async function logout() {
    setSigningOut(true);
    try {
      await post("/api/auth/logout");
    } catch (e) {
      // The cookie may still be valid, so stay signed in and let the user retry.
      toast.error(`Could not sign out: ${e instanceof Error ? e.message : "try again"}`);
      setSigningOut(false);
      return;
    }
    announceSessionChange();
    // Full page load clears every cached query from this session.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- a full load is the point: it empties the query cache
    window.location.href = "/login";
  }

  return (
    <div className="flex min-h-screen">
      <aside aria-label="Sidebar" className={cn("sticky top-0 hidden h-screen shrink-0 border-r bg-sidebar transition-[width] duration-200 motion-reduce:transition-none lg:block",
        collapsed ? "w-16" : "w-64")}>
        <SidebarContent collapsed={collapsed} onLogout={() => setConfirm(true)} onToggle={toggle} />
      </aside>
      <Sheet open={drawer} onOpenChange={setDrawer}>
        <SheetContent side="left" showCloseButton={false} className="w-72 gap-0 bg-sidebar p-0 lg:hidden">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <SheetDescription className="sr-only">Main navigation</SheetDescription>
          <SidebarContent collapsed={false} onNavigate={() => setDrawer(false)} onLogout={() => { setDrawer(false); setConfirm(true); }} />
        </SheetContent>
      </Sheet>
      <div className="flex min-w-0 flex-1 flex-col">
        <StatusBar leading={
          <Button size="icon" variant="outline" className="lg:hidden" aria-label="Open navigation" onClick={() => setDrawer(true)}>
            <Menu className="h-4 w-4 shrink-0" />
          </Button>
        } />
        <main className="flex-1 p-4 lg:p-6">{children}</main>
      </div>
      <ConfirmDialog open={confirm} onOpenChange={setConfirm} title="Sign out?" confirmLabel="Sign out" pending={signingOut}
        description="You will need to sign in again to review records." onConfirm={logout} />
    </div>
  );
}
