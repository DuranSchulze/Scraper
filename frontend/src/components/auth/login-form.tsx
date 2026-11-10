"use client";

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { ScanSearch } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
    <Card className="w-full max-w-sm">
      <CardHeader>
        <div className="mb-2 flex size-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <ScanSearch className="h-4 w-4 shrink-0" />
        </div>
        <CardTitle className="text-xl font-semibold tracking-tight">Workspace Scraper</CardTitle>
        <p className="text-sm text-muted-foreground">Sign in to review and publish client records.</p>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="email">Email</Label>
            <Input id="email" type="email" autoComplete="username" autoFocus required value={email}
              onChange={(e) => setEmail(e.target.value)} aria-invalid={!!error} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="password">Password</Label>
            <Input id="password" type="password" autoComplete="current-password" required value={password}
              onChange={(e) => setPassword(e.target.value)} aria-invalid={!!error} />
          </div>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <Button type="submit" disabled={pending || !email || !password}>{pending ? "Signing in…" : "Sign in"}</Button>
        </form>
      </CardContent>
    </Card>
  );
}
