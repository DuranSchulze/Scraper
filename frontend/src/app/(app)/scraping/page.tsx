import { Suspense } from "react";
import { ScrapingView } from "@/components/scrape/scraping-view";
import { RouteGuard } from "@/components/shell/route-guard";

export default function Page() {
  return (
    <RouteGuard route="scraping">
      <Suspense fallback={null}>
        <ScrapingView />
      </Suspense>
    </RouteGuard>
  );
}
