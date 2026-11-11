"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Menu as MenuPrimitive } from "@base-ui/react/menu";
import { ChevronsUpDown, ListChecks, LogOut, Menu, Moon, PanelLeftClose, PanelLeftOpen, ScanSearch, Sun, Users } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ConfirmDialog } from "@/components/review/confirm-dialog";
import { StatusBar } from "@/components/scrape/status-bar";
import { announceSessionChange, markSigningOut, meQuery, post, statusQuery } from "@/lib/api";
import { toggleTheme, useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";

// Nav stays plain data, grouped like modern app shells; admin-only groups are hidden for members (pages and the API check again).
const NAV = [
  { label: "Workspace", admin: false, items: [
    { href: "/", label: "Review", icon: ListChecks },
    { href: "/scraping", label: "Scraping", icon: ScanSearch },
  ] },
  { label: "Admin", admin: true, items: [
    { href: "/accounts", label: "Accounts", icon: Users },
  ] },
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
const ROW = "group/row relative flex h-9 w-full items-center rounded-md text-sm font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-sidebar-ring";
const SLOT = "flex w-12 shrink-0 items-center justify-center";
const ICON = "size-[1.125rem] shrink-0";
const IDLE = "text-sidebar-foreground/80 hover:bg-sidebar-accent/70 hover:text-sidebar-accent-foreground";
const ITEM = "flex h-8 cursor-default items-center gap-2 rounded-md px-2 text-sm outline-none select-none data-highlighted:bg-accent data-highlighted:text-accent-foreground [&_svg]:size-4 [&_svg]:shrink-0";

function NavRow({ href, label, icon: Icon, active, count, tip, onNavigate }: {
  href: string; label: string; icon: React.ElementType; active: boolean; count?: number; tip: boolean; onNavigate?: () => void;
}) {
  return (
    <Tooltip disabled={!tip}>
      <TooltipTrigger render={
        <Link href={href} onClick={onNavigate} aria-label={label} aria-current={active ? "page" : undefined}
          className={cn(ROW, active ? "bg-sidebar-accent font-semibold text-sidebar-accent-foreground" : IDLE)} />
      }>
        {active && <span aria-hidden className="absolute top-1/2 left-0 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-sidebar-primary" />}
        <span className={cn(SLOT, "relative")}>
          <Icon className={ICON} />
          {!!count && <span aria-hidden className={cn("absolute top-0.5 right-2 size-2.5 rounded-full bg-notify ring-2 not-in-data-[sidebar=collapsed]:hidden max-lg:hidden", active ? "ring-sidebar-accent" : "ring-sidebar")} />}
        </span>
        <span className="sb-label flex min-w-0 flex-1 items-center gap-2 pr-2 whitespace-nowrap">
          <span className="truncate">{label}</span>
          {!!count && (
            <span className="ml-auto rounded-full bg-sidebar-primary px-1.5 py-px text-xs font-semibold text-sidebar-primary-foreground tabular-nums">
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
  const groups = NAV.filter((g) => !g.admin || me?.role === "admin");
  const name = me?.name ?? "Account";
  const logo = (
    <span className="flex size-8 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
      <ScanSearch className="size-4 shrink-0" />
    </span>
  );
  return (
    <div className="flex h-full flex-col text-sidebar-foreground">
      <div className="flex h-14 shrink-0 items-center px-2">
        <span className={SLOT}>{logo}</span>
        <span className="sb-label min-w-0 flex-1 truncate pr-2 text-sm font-semibold tracking-tight whitespace-nowrap">Workspace Scraper</span>
      </div>
      <nav aria-label="Main" className="flex flex-1 flex-col gap-4 overflow-x-hidden overflow-y-auto px-2 py-2">
        {groups.map((g) => (
          <div key={g.label} role="group" aria-label={g.label} className="flex flex-col gap-0.5">
            <div className="relative h-7">
              <span aria-hidden className="sb-label absolute inset-y-0 left-3 flex items-center text-xs font-medium whitespace-nowrap text-sidebar-foreground/70">{g.label}</span>
              <span aria-hidden className="sb-icon-collapsed absolute top-1/2 left-4 h-px w-4 bg-sidebar-border" />
            </div>
            {g.items.map(({ href, label, icon }) => (
              <NavRow key={href} href={href} label={label} icon={icon} active={isActive(pathname, href)} tip={tip}
                count={href === "/" ? pending : undefined} onNavigate={onNavigate} />
            ))}
          </div>
        ))}
      </nav>
      <div className="flex flex-col gap-1 border-t border-sidebar-border p-2">
        {/* The whole row is the switch, so the icon alone still toggles it while the sidebar is collapsed. */}
        <Tooltip disabled={!tip}>
          <TooltipTrigger render={
            <button type="button" role="switch" aria-checked={theme === "dark"} aria-label="Dark mode" onClick={toggleTheme}
              className={cn(ROW, IDLE)} />
          }>
            <span className={SLOT}>{theme === "dark" ? <Moon className={ICON} /> : <Sun className={ICON} />}</span>
            <span className="sb-label flex min-w-0 flex-1 items-center gap-2 pr-2 whitespace-nowrap">
              <span className="truncate">Dark mode</span>
              <span aria-hidden className={cn("ml-auto flex h-5 w-9 shrink-0 items-center rounded-full p-0.5 transition-colors",
                theme === "dark" ? "bg-sidebar-primary" : "bg-sidebar-foreground/55")}>
                <span className={cn("size-4 rounded-full bg-white shadow-sm transition-transform", theme === "dark" && "translate-x-4")} />
              </span>
            </span>
          </TooltipTrigger>
          <TooltipContent side="right" sideOffset={10}>{theme === "dark" ? "Dark mode on" : "Dark mode off"}</TooltipContent>
        </Tooltip>
        {/* A labelled row (not a header icon), so the collapse control is easy to find. */}
        {showCollapse && (
          <Tooltip disabled={!tip}>
            <TooltipTrigger render={
              <button type="button" onClick={toggleCollapsed} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} aria-expanded={!collapsed}
                className={cn(ROW, IDLE)} />
            }>
              <span className={SLOT}>
                <PanelLeftClose className={cn("sb-icon-expanded", ICON)} />
                <PanelLeftOpen className={cn("sb-icon-collapsed", ICON)} />
              </span>
              <span className="sb-label pr-2 whitespace-nowrap">Collapse sidebar</span>
            </TooltipTrigger>
            <TooltipContent side="right" sideOffset={10}>Expand sidebar</TooltipContent>
          </Tooltip>
        )}
        <MenuPrimitive.Root>
          {/* Tooltip and menu share one button: the tooltip trigger renders the menu trigger. */}
          <Tooltip disabled={!tip}>
          <TooltipTrigger render={
            <MenuPrimitive.Trigger aria-label={`Account menu for ${name}`}
              className={cn(ROW, "h-12 text-left text-sidebar-foreground hover:bg-sidebar-accent/70 data-popup-open:bg-sidebar-accent")} />
          }>
            <span className={SLOT}>
              <span aria-hidden className="flex size-8 items-center justify-center rounded-full bg-sidebar-accent text-xs font-semibold text-sidebar-accent-foreground uppercase ring-1 ring-sidebar-border">{name.slice(0, 1)}</span>
            </span>
            <span className="sb-label flex min-w-0 flex-1 items-center gap-2 pr-2 whitespace-nowrap">
              <span className="min-w-0 flex-1 leading-tight">
                <span className="block truncate text-sm font-medium">{name}</span>
                {me && <span className="block truncate text-xs text-sidebar-foreground/70 capitalize">{me.role}</span>}
              </span>
              <ChevronsUpDown aria-hidden className="size-4 shrink-0 opacity-60" />
            </span>
          </TooltipTrigger>
          <TooltipContent side="right" sideOffset={10}>{`${name} · account`}</TooltipContent>
          </Tooltip>
          <MenuPrimitive.Portal>
            <MenuPrimitive.Positioner side={collapsed ? "right" : "top"} align={collapsed ? "end" : "start"} sideOffset={8} className="isolate z-50">
              <MenuPrimitive.Popup className="w-(--anchor-width) min-w-56 origin-(--transform-origin) rounded-lg bg-popover p-1 text-popover-foreground shadow-lg ring-1 ring-foreground/10 outline-none duration-100 data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95">
                {me && (
                  <div className="px-2 py-1.5 leading-tight">
                    <div className="truncate text-sm font-medium">{me.name}</div>
                    <div className="truncate text-xs text-muted-foreground capitalize">{me.role}</div>
                  </div>
                )}
                {me && <MenuPrimitive.Separator className="-mx-1 my-1 h-px bg-border" />}
                <MenuPrimitive.Item onClick={onLogout} className={cn(ITEM, "text-destructive data-highlighted:bg-destructive/10 data-highlighted:text-destructive")}>
                  <LogOut aria-hidden />
                  Sign out
                </MenuPrimitive.Item>
              </MenuPrimitive.Popup>
            </MenuPrimitive.Positioner>
          </MenuPrimitive.Portal>
        </MenuPrimitive.Root>
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
      {/* The spacer snaps to the new width (one page layout); only the fixed sidebar animates, so the page never re-lays out per frame. */}
      <div aria-hidden className="sb-spacer hidden shrink-0 lg:block" />
      <aside aria-label="Sidebar" className="sb-aside fixed inset-y-0 left-0 z-40 hidden lg:block">
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
