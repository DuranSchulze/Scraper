"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { keys, post, statusQuery } from "@/lib/api";
import { list } from "@/lib/format";
import { ConfirmDialog } from "@/components/review/confirm-dialog";
import { RulesDialog } from "@/components/review/text-dialog";

const SOURCES = [["gmail", "Gmail"], ["contacts", "Contacts"], ["sheets", "Sheets"]] as const;

export function ScrapeForm() {
  const qc = useQueryClient();
  const { data: s } = useQuery(statusQuery);
  const running = !!s?.running;
  const [sources, setSources] = useState<string[]>(["gmail", "contacts", "sheets"]);
  const [mailboxes, setMailboxes] = useState("");
  const [since, setSince] = useState("");
  const [full, setFull] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [rules, setRules] = useState(false);
  // The interrupted scrape is captured when the dialog opens, so a status refresh can never retarget Resume.
  const [resumeAsk, setResumeAsk] = useState<{ id: number; done: number } | null>(null);

  const started = () => { qc.invalidateQueries({ queryKey: keys.status }); qc.invalidateQueries({ queryKey: keys.board }); };
  const fail = (e: Error) => toast.error(e.message);
  const scrape = useMutation({
    mutationFn: () => post("/api/scrape", { sources, mailboxes: list(mailboxes), since, full }),
    onSuccess: () => { toast.success("Scrape started"); started(); }, onError: fail,
  });
  const resume = useMutation({
    mutationFn: (id: number) => post(`/api/jobs/${id}/resume`),
    onSuccess: () => { setResumeAsk(null); toast.success("Resumed the interrupted scrape"); started(); },
    onError: fail,
  });
  // An interrupted scrape with finished mailboxes is worth keeping: starting a new one would begin again at 0.
  const doneMailboxes = Array.isArray(s?.job_params?.done_mailboxes) ? s.job_params.done_mailboxes.length : 0;
  const interrupted = s?.job === "scrape" && !!s.job_id && !!s.job_status && ["interrupted", "stopped", "failed"].includes(s.job_status) && doneMailboxes > 0;
  const extract = useMutation({
    mutationFn: () => post("/api/extract", { full: true }),
    onSuccess: () => { setConfirm(false); toast.success("Extraction started"); started(); }, onError: fail,
  });

  return (
    <Card>
      <CardHeader><CardTitle className="text-base font-medium text-muted-foreground">Scrape</CardTitle></CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex flex-wrap gap-4">
          {SOURCES.map(([v, label]) => (
            <Label key={v} className="gap-2 font-normal">
              <Checkbox checked={sources.includes(v)}
                onCheckedChange={(on) => setSources((x) => on ? [...x, v] : x.filter((y) => y !== v))} />
              {label}
            </Label>
          ))}
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="mailboxes" className="text-xs text-muted-foreground">Mailboxes (comma list, blank = all users)</Label>
          <Input id="mailboxes" value={mailboxes} onChange={(e) => setMailboxes(e.target.value)} placeholder="sales@company.com, jane@company.com" />
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1">
            <Label htmlFor="since" className="text-xs text-muted-foreground">Since</Label>
            <Input id="since" type="date" value={since} onChange={(e) => setSince(e.target.value)} className="w-40" />
          </div>
          <Label className="mb-2 gap-2 font-normal"><Checkbox checked={full} onCheckedChange={(v) => setFull(!!v)} /> Full rescan</Label>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button disabled={!s || running || scrape.isPending || !sources.length} onClick={() => (interrupted && s?.job_id ? setResumeAsk({ id: s.job_id, done: doneMailboxes }) : scrape.mutate())}>
            {scrape.isPending ? "Starting…" : "Run scrape"}
          </Button>
          <Button variant="outline" disabled={running} onClick={() => setConfirm(true)}>Re-run extraction</Button>
          <Button variant="ghost" onClick={() => setRules(true)}>View rules</Button>
        </div>
        {running && <p className="text-xs text-muted-foreground">A job is running or paused; new jobs start when it ends.</p>}
        {s && (
          <p className="border-t pt-3 text-xs text-muted-foreground tabular-nums">
            Stored: {(s.raw_items.gmail ?? 0).toLocaleString()} emails · {(s.raw_items.contacts ?? 0).toLocaleString()} contacts ·
            {" "}{(s.raw_items.sheets ?? 0).toLocaleString()} sheet rows
          </p>
        )}
      </CardContent>

      <ConfirmDialog open={confirm} onOpenChange={setConfirm}
        title="Re-run extraction?" confirmLabel="Re-run extraction" pending={extract.isPending}
        description="Re-reads every stored item with the current rules. Pending records are rebuilt; approved and rejected ones are kept. Nothing is downloaded from Google."
        onConfirm={() => extract.mutate()} />
      <Dialog open={!!resumeAsk} onOpenChange={(o) => { if (!o && !resume.isPending && !scrape.isPending) setResumeAsk(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Resume the interrupted scrape?</DialogTitle>
            <DialogDescription>
              The last scrape stopped with {resumeAsk?.done ?? 0} mailbox{resumeAsk?.done === 1 ? "" : "es"} already finished. Resume keeps them and
              continues with the original settings. A new scrape starts again from the first mailbox: only new mail is downloaded, but
              contacts and sheets are fetched again.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setResumeAsk(null)} disabled={resume.isPending || scrape.isPending}>Cancel</Button>
            <Button variant="outline" disabled={resume.isPending || scrape.isPending}
              onClick={() => scrape.mutate(undefined, { onSettled: () => setResumeAsk(null) })}>
              {scrape.isPending ? "Starting…" : "Start new scrape"}
            </Button>
            <Button disabled={resume.isPending || scrape.isPending} onClick={() => resumeAsk && resume.mutate(resumeAsk.id)}>
              {resume.isPending ? "Resuming…" : "Resume"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {rules && <RulesDialog onClose={() => setRules(false)} />}
    </Card>
  );
}
