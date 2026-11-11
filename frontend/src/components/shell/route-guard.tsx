"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { LoadingLabel, Skeleton } from "@/components/ui/skeleton";
import { canUse, homeFor, meQuery } from "@/lib/api";
import type { RouteKey } from "@/lib/types";

/** Generic page placeholder: title, subtitle and a content card, the shape every page here starts with. */
export function PageSkeleton({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex flex-col gap-4">
      <LoadingLabel>{label}</LoadingLabel>
      <div className="flex flex-col gap-2">
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-4 w-full max-w-md" />
      </div>
      <div className="flex flex-col gap-3 rounded-xl border bg-card p-4">
        <div className="flex flex-wrap gap-2">
          <Skeleton className="h-8 w-48 rounded-full" />
          <Skeleton className="h-8 w-28 rounded-full" />
          <Skeleton className="h-8 w-28 rounded-full" />
        </div>
        {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-10 w-full" />)}
      </div>
    </div>
  );
}

/** Mounts a page only once the signed-in account may use it; otherwise sends it to its first allowed page. */
export function RouteGuard({ route, children }: { route: RouteKey; children: React.ReactNode }) {
  const router = useRouter();
  const { data: me } = useQuery(meQuery);
  const allowed = canUse(me, route);
  useEffect(() => {
    if (me && !allowed) router.replace(homeFor(me));
  }, [me, allowed, router]);
  // Nothing protected mounts (or fetches) until permissions are known.
  if (!allowed) return <PageSkeleton label={me ? "Redirecting…" : "Loading…"} />;
  return children;
}
