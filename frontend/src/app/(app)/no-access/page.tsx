"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { DoorClosed } from "lucide-react";
import { PageSkeleton } from "@/components/shell/route-guard";
import { homeFor, meQuery, NO_PAGE } from "@/lib/api";

/** Fallback for an account whose role opens no page in this app. */
export default function Page() {
  const router = useRouter();
  const { data: me } = useQuery(meQuery);
  const home = me ? homeFor(me) : null;
  // Grants re-read on focus: once the role gains a page, go there.
  useEffect(() => { if (home && home !== NO_PAGE) router.replace(home); }, [home, router]);
  if (!me || home !== NO_PAGE) return <PageSkeleton label={me ? "Redirecting…" : "Loading…"} />;
  return (
    <div className="mx-auto flex max-w-md flex-col items-center gap-3 rounded-xl bg-muted/40 px-6 py-12 text-center">
      <span className="flex size-12 items-center justify-center rounded-full bg-primary/10 text-primary-text">
        <DoorClosed aria-hidden className="size-6 shrink-0" />
      </span>
      <h1 className="text-xl font-semibold tracking-tight">Nothing to open here</h1>
      <p className="text-sm text-muted-foreground">
        Your role, {me.role.name}, has no pages in the scraper. Ask someone who manages accounts to change your role.
      </p>
    </div>
  );
}
