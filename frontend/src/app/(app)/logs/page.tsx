import { Suspense } from "react";
import { LogsView } from "@/components/logs/logs-view";
import { RouteGuard } from "@/components/shell/route-guard";

export default function Page() {
  return (
    <RouteGuard route="scraping">
      <Suspense fallback={null}>
        <LogsView />
      </Suspense>
    </RouteGuard>
  );
}
