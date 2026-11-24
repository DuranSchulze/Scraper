"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Pick } from "@/components/review/pick";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { keys, scheduleQuery, updateSchedule } from "@/lib/api";
import { ago, phDateTime } from "@/lib/format";
import { GRACE_SECONDS, Spinner, useGuarded } from "@/lib/guarded";
import { ConfirmDialog } from "@/components/review/confirm-dialog";

const label = (m: number) => m < 60 ? `Every ${m} minutes` : m === 60 ? "Every hour" : m % 60 === 0 ? `Every ${m / 60} hours` : `Every ${m} minutes`;
const PRESETS = [120, 240, 360, 720, 1440];

export function ScheduleCard() {
  const qc = useQueryClient();
  const { data, isError, error, refetch } = useQuery(scheduleQuery);
  // null = follow the server; set once the user edits, cleared after a save.
  const [draft, setDraft] = useState<{ enabled: boolean; interval: number } | null>(null);
  const enabled = draft?.enabled ?? data?.enabled ?? false;
  const interval = draft?.interval ?? data?.interval_minutes ?? 120;
  const [asking, setAsking] = useState(false);
  const guard = useGuarded("schedule");
  const save = useMutation({
    mutationFn: (v: { enabled: boolean; interval: number }) => updateSchedule({ enabled: v.enabled, interval_minutes: v.interval }),
    // Keep any edit made while the request was pending: only clear the draft if it is what was saved.
    onSuccess: (r, v) => { qc.setQueryData(keys.schedule, r); setDraft((d) => (d && d.enabled === v.enabled && d.interval === v.interval ? null : d)); toast.success(r.enabled ? "Automatic scraping saved" : "Automatic scraping turned off"); },
    onError: (e: Error) => toast.error(e.message),
  });
  // A server value outside the presets (set from SCRAPE_EVERY_MINUTES) stays selectable.
  const options = [...new Set([...PRESETS, interval])].sort((a, b) => a - b).map((m) => ({ value: String(m), label: label(m) }));
  const next = !data ? "" : !data.enabled ? "Off" : data.next_run_at ? phDateTime(data.next_run_at) : "After the current job ends";

  return (
    <Card>
      <CardHeader><CardTitle className="text-base font-medium text-muted-foreground">Automatic scraping</CardTitle></CardHeader>
      <CardContent className="flex flex-col gap-3">
        {isError ? (
          <div role="alert" className="flex flex-col items-start gap-2 text-sm">
            <p className="text-destructive">{error.message}</p>
            <Button variant="outline" size="sm" onClick={() => refetch()}>Retry</Button>
          </div>
        ) : !data ? (
          <div className="flex flex-col gap-3" aria-busy="true"><Skeleton className="h-6 w-40" /><Skeleton className="h-9 w-full" /></div>
        ) : (
          <>
            <Label className="justify-between gap-3 font-normal">
              Scrape new emails automatically
              <Switch checked={enabled} onCheckedChange={(on) => setDraft({ enabled: on, interval })} />
            </Label>
            <div className="flex flex-col gap-1">
              <Label htmlFor="schedule-interval" className="text-xs text-muted-foreground">Interval</Label>
              <Pick id="schedule-interval" label="Interval" value={String(interval)} options={options}
                onChange={(v) => setDraft({ enabled, interval: Number(v) })} />
            </div>
            <Button className="self-start" disabled={!draft || guard.busy} aria-busy={guard.running} onClick={() => setAsking(true)}>
              {guard.busyLabel ? <><Spinner />{guard.busyLabel}</> : "Save"}
            </Button>
            <ConfirmDialog open={asking} onOpenChange={setAsking} guard={guard} confirmLabel="Save schedule"
              title={enabled ? "Save automatic scraping?" : "Turn off automatic scraping?"}
              description={`${enabled ? `New emails will be scraped ${label(interval).toLowerCase()}.` : "No scrape will start on its own until you turn it on again."} You get ${GRACE_SECONDS} seconds to undo before anything is sent.`}
              onConfirm={() => {
                setAsking(false);
                const v = { enabled, interval };
                guard.start({ verb: "Saving", what: "Saving automatic scraping", run: () => save.mutateAsync(v) });
              }} />
            <p className="border-t pt-3 text-xs text-muted-foreground tabular-nums">
              Last auto run: {data.last_auto_at ? ago(data.last_auto_at) : "never"} · Next run: {next}
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}
