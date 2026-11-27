"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Check, CircleAlert, X } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import { ApiError, changePassword, homeFor, markSigningOut, meQuery, post, announceSessionChange } from "@/lib/api";
import { canSubmit, passwordRules as rules } from "@/lib/password";

export function ChangePasswordForm() {
  const { data: me, error: meError, refetch, isFetching } = useQuery(meQuery);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  // This page sits outside AppShell, so it listens itself: another tab signed in or out, so reload,
  // which drops the typed passwords instead of leaving them on screen for the next account.
  useEffect(() => {
    let ch: BroadcastChannel;
    try { ch = new BroadcastChannel("scraper-session"); } catch { return; }
    ch.onmessage = () => window.location.reload();
    return () => ch.close();
  }, []);

  // A 401 already sent the browser to /login; anything else (API down) needs a way out of the skeleton.
  if (!me && meError) {
    return (
      <div role="alert" className="flex w-full max-w-sm flex-col gap-3">
        <h1 className="text-xl font-semibold tracking-tight">Couldn&apos;t check your account</h1>
        <p className="text-sm text-muted-foreground">{meError.message}</p>
        <Button className="self-start" onClick={() => refetch()} disabled={isFetching}>{isFetching ? "Retrying…" : "Try again"}</Button>
      </div>
    );
  }

  if (!me) {
    return (
      <div className="flex w-full max-w-sm flex-col gap-4" aria-busy="true">
        <span className="sr-only" role="status">Loading…</span>
        <Skeleton className="h-7 w-48" />
        <Skeleton className="h-4 w-full" />
        {[0, 1, 2].map((i) => <Skeleton key={i} className="h-10 w-full" />)}
      </div>
    );
  }

  const forced = me.must_change_password;
  const checks = rules(next, me.email);
  const strong = canSubmit(next, me.email);
  const matches = !!confirm && next === confirm;
  const type = show ? "text" : "password";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!current || !strong || !matches) return;
    setPending(true);
    setError("");
    try {
      const user = await changePassword(current, next);
      // Full navigation: the API set a fresh session cookie; reload with it and an empty cache.
      window.location.href = homeFor(user);
    } catch (err) {
      const wait = err instanceof ApiError && err.status === 429 ? Math.max(1, Math.ceil((err.retryAfter ?? 60) / 60)) : 0;
      setError(wait ? `Too many attempts. Try again in about ${wait} minute${wait > 1 ? "s" : ""}.`
        : err instanceof Error ? err.message : "Could not change the password.");
      setPending(false);
    }
  }

  async function signOut() {
    setPending(true);
    markSigningOut();
    try {
      await post("/api/auth/logout");
    } catch (e) {
      // The cookie may still be valid, so stay here and let the user retry.
      toast.error(`Could not sign out: ${e instanceof Error ? e.message : "try again"}`);
      markSigningOut(false);
      setPending(false);
      return;
    }
    announceSessionChange();
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- a full load is the point: it empties the query cache
    window.location.href = "/login";
  }

  return (
    <div className="w-full max-w-sm">
      <h1 className="text-2xl font-semibold tracking-tight">{forced ? "Set your password" : "Change password"}</h1>
      <p className="mt-1.5 text-sm text-muted-foreground">
        {forced
          ? `Hi ${me.name}. You signed in with a temporary password. Choose your own to continue.`
          : "Other devices signed in to this account will be signed out."}
      </p>
      <form onSubmit={submit} className="mt-8 flex flex-col gap-5" noValidate>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="current">{forced ? "Temporary password" : "Current password"}</Label>
          <Input id="current" type={type} autoComplete="current-password" autoFocus required value={current} className="h-10"
            onChange={(e) => setCurrent(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="new">New password</Label>
          <Input id="new" type={type} autoComplete="new-password" required value={next} className="h-10"
            aria-describedby="new-rules" aria-invalid={!!next && !strong} onChange={(e) => setNext(e.target.value)} />
          <ul id="new-rules" aria-label="Password requirements" className="mt-1 grid gap-1 text-sm sm:grid-cols-2">
            {checks.map((c) => (
              <li key={c.label} className={c.ok ? "flex items-center gap-1.5 text-success" : "flex items-center gap-1.5 text-muted-foreground"}>
                {c.ok ? <Check aria-hidden className="h-3.5 w-3.5 shrink-0" /> : <X aria-hidden className="h-3.5 w-3.5 shrink-0" />}
                <span>{c.label}<span className="sr-only">{c.ok ? ": met" : ": not met"}</span></span>
              </li>
            ))}
          </ul>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="confirm">Confirm new password</Label>
          <Input id="confirm" type={type} autoComplete="new-password" required value={confirm} className="h-10"
            aria-invalid={!!confirm && !matches} aria-describedby={confirm && !matches ? "confirm-error" : undefined}
            onChange={(e) => setConfirm(e.target.value)} />
          {confirm && !matches && <p id="confirm-error" className="text-sm text-destructive">Passwords don&apos;t match.</p>}
        </div>
        <label className="flex cursor-pointer items-center gap-2 text-sm">
          <Checkbox checked={show} onCheckedChange={(v) => setShow(!!v)} /> Show passwords
        </label>
        {error && (
          <p role="alert" className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" />{error}
          </p>
        )}
        <Button type="submit" size="lg" className="h-10 w-full" disabled={pending || !current || !strong || !matches}>
          {pending ? "Saving…" : forced ? "Set password and continue" : "Change password"}
        </Button>
        {forced
          ? <Button type="button" variant="ghost" onClick={signOut} disabled={pending}>Sign out</Button>
          // Once sent, the change commits even if the user leaves, so Cancel waits for the answer.
          : pending
            ? <Button type="button" variant="ghost" disabled>Cancel</Button>
            : <Link href={homeFor(me)} className={buttonVariants({ variant: "ghost" })}>Cancel</Link>}
      </form>
    </div>
  );
}
