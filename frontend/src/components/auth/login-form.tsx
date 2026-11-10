"use client";

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { CircleAlert, Eye, EyeOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, announceSessionChange, post } from "@/lib/api";
import type { User } from "@/lib/types";

/** Only same-origin paths are followed after sign-in; the browser's own URL parser decides what is same-origin. */
function safeNext(v: string | null) {
  if (!v || !v.startsWith("/") || /[\\\u0000-\u001f\u007f]/.test(v)) return "/";
  try {
    const url = new URL(v, window.location.origin);
    if (url.origin !== window.location.origin || url.pathname.startsWith("/login")) return "/";
    // Navigate to the checked absolute URL: a path like //host (after ".." folding) must not be re-parsed.
    return url.href;
  } catch { return "/"; }
}

export function LoginForm() {
  const requested = useSearchParams().get("next");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [show, setShow] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setError("");
    try {
      await post<User>("/api/auth/login", { email, password });
      announceSessionChange();
      // Full navigation: starts with an empty query cache for this account.
      window.location.href = safeNext(requested);
    } catch (err) {
      const wait = err instanceof ApiError && err.status === 429 ? Math.max(1, Math.ceil((err.retryAfter ?? 60) / 60)) : 0;
      setError(wait ? `Too many attempts. Try again in about ${wait} minute${wait > 1 ? "s" : ""}.`
        : err instanceof ApiError && err.status === 401 ? "Invalid email or password."
        : err instanceof Error ? err.message : "Could not sign in.");
      setPending(false);
    }
  }

  return (
    <div className="w-full max-w-sm">
      <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>
      <p className="mt-1.5 text-sm text-muted-foreground">Use the account an administrator created for you.</p>
      <form onSubmit={submit} className="mt-8 flex flex-col gap-5" noValidate>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="email">Email</Label>
          <Input id="email" type="email" autoComplete="username" autoFocus required value={email} className="h-10"
            onChange={(e) => setEmail(e.target.value)} aria-invalid={!!error} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="password">Password</Label>
          <div className="relative">
            <Input id="password" type={show ? "text" : "password"} autoComplete="current-password" required value={password}
              className="h-10 pr-10" onChange={(e) => setPassword(e.target.value)} aria-invalid={!!error} />
            <button type="button" onClick={() => setShow((v) => !v)} aria-label={show ? "Hide password" : "Show password"} aria-pressed={show}
              className="absolute top-1/2 right-1 flex size-8 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground transition-colors outline-none hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring">
              {show ? <EyeOff className="h-4 w-4 shrink-0" /> : <Eye className="h-4 w-4 shrink-0" />}
            </button>
          </div>
        </div>
        {error && (
          <p role="alert" className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" />{error}
          </p>
        )}
        <Button type="submit" size="lg" className="h-10 w-full" disabled={pending || !email || !password}>{pending ? "Signing in…" : "Sign in"}</Button>
      </form>
      <p className="mt-6 text-center text-xs text-muted-foreground">No account yet? Ask an administrator to create one.</p>
    </div>
  );
}
