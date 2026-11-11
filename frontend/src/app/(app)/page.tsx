import { Suspense } from "react";
import { ReviewApp } from "@/components/review/review-app";
import { RouteGuard } from "@/components/shell/route-guard";

// Filters and the open record live in the URL, so the view survives reloads and restarts.
export default function Page() {
  return (
    <RouteGuard route="review">
      <Suspense fallback={<main className="p-6 text-sm text-muted-foreground">Loading…</main>}>
        <ReviewApp />
      </Suspense>
    </RouteGuard>
  );
}
