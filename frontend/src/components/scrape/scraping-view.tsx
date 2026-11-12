"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { PageHeader } from "@/components/shell/page-header";
import { LogPanel } from "./log-panel";
import { MailboxBoard } from "./mailbox-board";
import { ScheduleCard } from "./schedule-card";
import { ScrapeForm } from "./scrape-form";

export function ScrapingView() {
  const router = useRouter(), pathname = usePathname(), params = useSearchParams();
  const mailbox = params.get("mailbox") || "";
  const select = (mb: string) => router.replace(mb ? `${pathname}?mailbox=${encodeURIComponent(mb)}` : pathname, { scroll: false });
  return (
    <div className="flex flex-col gap-4">
      <PageHeader title="Scraping" description="Start a scrape or extraction and follow each mailbox as it runs." />
      <div className="grid gap-4 xl:grid-cols-[minmax(0,24rem)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <ScrapeForm />
          <ScheduleCard />
          <MailboxBoard selected={mailbox} onSelect={select} />
        </div>
        <LogPanel mailbox={mailbox} onClearMailbox={() => select("")} />
      </div>
    </div>
  );
}
