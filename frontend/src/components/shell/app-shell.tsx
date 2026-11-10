"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ListChecks, LogOut, Menu, Moon, PanelLeftClose, PanelLeftOpen, ScanSearch, Sun, Users } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ConfirmDialog } from "@/components/review/confirm-dialog";
import { StatusBar } from "@/components/scrape/status-bar";
import { announceSessionChange, markSigningOut, meQuery, post, statusQuery } from "@/lib/api";
import { toggleTheme, useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";

// Nav stays plain data; admin-only items are hidden for members (pages and the API check again).
const NAV = [
  { href: "/", label: "Review", icon: ListChecks, admin: false },
  { href: "/scraping", label: "Scraping", icon: ScanSearch, admin: false },
  { href: "/accounts", label: "Accounts", icon: Users, admin: true },
] as const;

// The saved choice lives on <html data-sidebar> (set before paint by the inline script in the root layout);
// the sidebar's look is pure CSS (globals.css), React only reads it for tooltips and aria.
const COLLAPSE_KEY = "scraper.sidebar.collapsed";
const COLLAPSE_EVENT = "scraper:sidebar";
const isCollapsed = () => document.documentElement.dataset.sidebar === "collapsed";
const subscribeCollapse = (cb: () => void) => {
  const onStorage = (e: StorageEvent) => {
    if (e.key !== COLLAPSE_KEY) return;
    document.documentElement.dataset.sidebar = e.newValue === "1" ? "collapsed" : "expanded";
    cb();
  };
  window.addEventListener(COLLAPSE_EVENT, cb);
  window.addEventListener("storage", onStorage);
  return () => { window.removeEventListener(COLLAPSE_EVENT, cb); window.removeEventListener("storage", onStorage); };
};
function toggleCollapsed() {
  const next = !isCollapsed();
  document.documentElement.dataset.sidebar = next ? "collapsed" : "expanded";
  try { localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0"); } catch { /* storage unavailable */ }
  window.dispatchEvent(new Event(COLLAPSE_EVENT));
}

const isActive = (pathname: string, href: string) =>
  href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(href + "/");

// Every row keeps its icon in a fixed 3rem slot, so icons never move while the width animates and labels are only clipped.
const ROW = "group/row flex h-11 w-full items-center rounded-lg text-sm font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-sidebar-ring";
const SLOT = "flex w-12 shrink-0 items-center justify-center";

function NavRow({ href, label, icon: Icon, active, count, tip, onNavigate }: {
  href: string; label: string; icon: React.ElementType; active: boolean; count?: number; tip: boolean; onNavigate?: () => void;
}) {
  return (
    <Tooltip disabled={!tip}>
      <TooltipTrigger render={
        <Link href={href} onClick={onNavigate} aria-label={label} aria-current={active ? "page" : undefined}
          className={cn(ROW, active
            ? "bg-sidebar-primary font-semibold text-sidebar-primary-foreground shadow-sm"
            : "text-sidebar-foreground/85 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground")} />
      }>
        <span className={cn(SLOT, "relative")}>
          <Icon className="h-[1.15rem] w-[1.15rem] shrink-0" />
          {!!count && <span aria-hidden className={cn("absolute top-1 right-1.5 h-2.5 w-2.5 rounded-full bg-notify ring-2 not-in-data-[sidebar=collapsed]:hidden max-lg:hidden", active ? "ring-sidebar-primary" : "ring-sidebar")} />}
        </span>
        <span className="sb-label flex min-w-0 flex-1 items-center gap-2 pr-3 whitespace-nowrap">
          <span className="truncate">{label}</span>
          {!!count && (
            <span className={cn("ml-auto rounded-full px-1.5 py-px text-xs font-semibold tabular-nums",
              active ? "bg-sidebar-primary-foreground text-sidebar-primary" : "bg-sidebar-primary text-sidebar-primary-foreground")}>
              {count > 99 ? "99+" : count}
            </span>
          )}
        </span>
      </TooltipTrigger>
      <TooltipContent side="right" sideOffset={10}>{label}</TooltipContent>
    </Tooltip>
  );
}

function SidebarContent({ tip, collapsed, showCollapse, onNavigate, onLogout }: { tip: boolean; collapsed: boolean; showCollapse: boolean; onNavigate?: () => void; onLogout: () => void }) {
  const theme = useTheme();
  const pathname = usePathname();
  const { data: me } = useQuery(meQuery);
  const { data: status } = useQuery(statusQuery);
  const pending = status?.candidates.pending;
  const items = NAV.filter((n) => !n.admin || me?.role === "admin");
  return (
    <div className="flex h-full flex-col text-sidebar-foreground">
      <div className="flex h-16 shrink-0 items-center border-b border-sidebar-border">
        <span className={SLOT}>
          <span className="flex size-9 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
            <ScanSearch className="h-[1.15rem] w-[1.15rem] shrink-0" />
          </span>
        </span>
        <span className="sb-label truncate pr-3 text-[0.95rem] font-semibold tracking-tight whitespace-nowrap">Workspace Scraper</span>
      </div>
      <nav aria-label="Main" className="flex flex-1 flex-col gap-1 p-2">
        {items.map(({ href, label, icon }) => (
          <NavRow key={href} href={href} label={label} icon={icon} active={isActive(pathname, href)} tip={tip}
            count={href === "/" ? pending : undefined} onNavigate={onNavigate} />
        ))}
      </nav>
      <div className="flex flex-col gap-1 border-t border-sidebar-border p-2">
        {me && (
          <div className="flex h-12 items-center rounded-lg">
            <span className={SLOT}>
              <span aria-hidden className="flex size-8 items-center justify-center rounded-full bg-sidebar-accent text-xs font-semibold uppercase">{me.name.slice(0, 1)}</span>
            </span>
            <span className="sb-label min-w-0 pr-3 leading-tight whitespace-nowrap">
              <span className="block truncate text-sm font-medium">{me.name}</span>
              <span className="block truncate text-xs text-sidebar-foreground/80 capitalize">{me.role}</span>
            </span>
          </div>
        )}
        <Tooltip disabled={!tip}>
          <TooltipTrigger render={
            <button type="button" onClick={onLogout} aria-label="Sign out"
              className={cn(ROW, "text-sidebar-foreground/85 hover:bg-destructive/15 hover:text-destructive")} />
          }>
            <span className={SLOT}><LogOut className="h-[1.15rem] w-[1.15rem] shrink-0" /></span>
            <span className="sb-label pr-3 whitespace-nowrap">Sign out</span>
          </TooltipTrigger>
          <TooltipContent side="right" sideOffset={10}>Sign out</TooltipContent>
        </Tooltip>
        <Tooltip disabled={!tip}>
          <TooltipTrigger render={
            <button type="button" onClick={toggleTheme} aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
              className={cn(ROW, "text-sidebar-foreground/85 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground")} />
          }>
            <span className={SLOT}>{theme === "dark" ? <Sun className="h-[1.15rem] w-[1.15rem] shrink-0" /> : <Moon className="h-[1.15rem] w-[1.15rem] shrink-0" />}</span>
            <span className="sb-label pr-3 whitespace-nowrap">{theme === "dark" ? "Light mode" : "Dark mode"}</span>
          </TooltipTrigger>
          <TooltipContent side="right" sideOffset={10}>{theme === "dark" ? "Light mode" : "Dark mode"}</TooltipContent>
        </Tooltip>
        {showCollapse && (
          <Tooltip disabled={!tip}>
            <TooltipTrigger render={
              <button type="button" onClick={toggleCollapsed} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} aria-expanded={!collapsed}
                className={cn(ROW, "text-sidebar-foreground/85 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground")} />
            }>
              <span className={SLOT}>
                <PanelLeftClose className="sb-icon-expanded h-[1.15rem] w-[1.15rem] shrink-0" />
                <PanelLeftOpen className="sb-icon-collapsed h-[1.15rem] w-[1.15rem] shrink-0" />
              </span>
              <span className="sb-label pr-3 whitespace-nowrap">Collapse</span>
            </TooltipTrigger>
            <TooltipContent side="right" sideOffset={10}>Expand sidebar</TooltipContent>
          </Tooltip>
        )}
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const collapsed = useSyncExternalStore(subscribeCollapse, isCollapsed, () => false);
  const [drawer, setDrawer] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  // Turn transitions on after the first painted frame, so the saved state never animates in on load.
  useEffect(() => {
    const id = requestAnimationFrame(() => requestAnimationFrame(() => { document.documentElement.dataset.sidebarReady = ""; }));
    return () => cancelAnimationFrame(id);
  }, []);

  // Another tab signed in or out: reload so this one never shows the previous account's cached data.
  useEffect(() => {
    let ch: BroadcastChannel;
    try { ch = new BroadcastChannel("scraper-session"); } catch { return; }
    ch.onmessage = () => window.location.reload();
    return () => ch.close();
  }, []);

  async function logout() {
    setSigningOut(true);
    markSigningOut();
    try {
      await post("/api/auth/logout");
    } catch (e) {
      // The cookie may still be valid, so stay signed in and let the user retry.
      toast.error(`Could not sign out: ${e instanceof Error ? e.message : "try again"}`);
      setSigningOut(false);
      markSigningOut(false);
      return;
    }
    announceSessionChange();
    // Full page load clears every cached query from this session.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- a full load is the point: it empties the query cache
    window.location.href = "/login";
  }

  return (
    <div className="flex min-h-screen">
      <aside aria-label="Sidebar" className="sb-aside sticky top-0 z-40 hidden h-screen shrink-0 lg:block">
        <div className="h-full overflow-hidden bg-sidebar">
          <SidebarContent tip={collapsed} collapsed={collapsed} showCollapse onLogout={() => setConfirm(true)} />
        </div>
      </aside>
      <Sheet open={drawer} onOpenChange={setDrawer}>
        <SheetContent side="left" showCloseButton={false} className="w-72 gap-0 border-sidebar-border bg-sidebar p-0 lg:hidden">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <SheetDescription className="sr-only">Main navigation</SheetDescription>
          <SidebarContent tip={false} collapsed={false} showCollapse={false} onNavigate={() => setDrawer(false)} onLogout={() => { setDrawer(false); setConfirm(true); }} />
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
