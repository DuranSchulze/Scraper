import { Suspense } from "react";
import { ReviewApp } from "@/components/review/review-app";

// Filters and the open record live in the URL, so the view survives reloads and restarts.
export default function Page() {
  return (
    <Suspense fallback={<main className="p-6 text-sm text-muted-foreground">Loading…</main>}>
      <ReviewApp />
    </Suspense>
  );
}
