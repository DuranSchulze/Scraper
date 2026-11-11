"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { canUse, homeFor, meQuery } from "@/lib/api";
import type { RouteKey } from "@/lib/types";

/** Mounts a page only once the signed-in account may use it; otherwise sends it to its first allowed page. */
export function RouteGuard({ route, children }: { route: RouteKey; children: React.ReactNode }) {
  const router = useRouter();
  const { data: me } = useQuery(meQuery);
  const allowed = canUse(me, route);
  useEffect(() => {
    if (me && !allowed) router.replace(homeFor(me));
  }, [me, allowed, router]);
  // Nothing protected mounts (or fetches) until permissions are known.
  if (!allowed) return <p role="status" className="py-8 text-center text-sm text-muted-foreground">{me ? "Redirecting…" : "Loading…"}</p>;
  return children;
}
