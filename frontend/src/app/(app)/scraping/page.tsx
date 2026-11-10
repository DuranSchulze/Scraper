import { Suspense } from "react";
import { ScrapingView } from "@/components/scrape/scraping-view";

export default function Page() {
  return (
    <Suspense fallback={null}>
      <ScrapingView />
    </Suspense>
  );
}
