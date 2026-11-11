"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ChevronDown, ChevronUp, Mail, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SheetClose, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { candidateQuery, keys, post } from "@/lib/api";
import { day, money, stamp } from "@/lib/format";
import { STAGES, STATUSES, type Candidate, type CandidateFields, type Field } from "@/lib/types";
import { Pill, StageBadge } from "./badges";
import { ClientDetails } from "./client-details";
import { clearDraft, initialDraft, loadDraft, saveDraft, toClientIn, validate, type Draft } from "./draft";
import { Pick } from "./pick";
import { SourceDialog } from "./text-dialog";
import { LoadingLabel, Skeleton } from "@/components/ui/skeleton";

type Nav = { prevId: number | null; nextId: number | null; onNav: (id: number) => void; position: string | null };

/** Drawer body: header with prev/next, tabs that scroll, and actions pinned at the bottom. */
/** onDone gets the record to open next (null closes the drawer). */
export function RecordDetail({ id, onDone, ...nav }: { id: number; onDone: (next: number | null) => void } & Nav) {
  const { data, error } = useQuery(candidateQuery(id));
  if (data) return <DetailForm key={`${data.id}:${data.email_key}`} c={data} onDone={onDone} nav={nav} />;
  return (
    <div className="flex h-full flex-col">
      <DrawerHeader title={`Record #${id}`} subtitle={error ? "" : "Loading…"} nav={nav} />
      {error ? <p role="alert" className="p-4 text-sm text-destructive">Could not load record #{id}: {error.message}</p>
        : <DetailSkeleton />}
    </div>
  );
}

/** Drawer placeholder: tab strip, labelled fields and the pinned action bar. */
function DetailSkeleton() {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <LoadingLabel>Loading record…</LoadingLabel>
      <div className="flex gap-2 border-b px-5 py-2">{[16, 20, 18].map((w, i) => <Skeleton key={i} className="h-7" style={{ width: `${w * 4}px` }} />)}</div>
      <div className="flex flex-1 flex-col gap-4 overflow-hidden p-5">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="flex flex-col gap-1.5"><Skeleton className="h-3 w-24" /><Skeleton className="h-9 w-full" /></div>
        ))}
      </div>
      <div className="flex justify-end gap-2 border-t px-5 py-3"><Skeleton className="h-8 w-20" /><Skeleton className="h-8 w-32" /></div>
    </div>
  );
}

function DrawerHeader({ title, subtitle, nav, children }: { title: string; subtitle: string; nav: Nav; children?: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2 border-b px-5 pt-4 pb-3">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <SheetTitle className="text-lg font-semibold tracking-tight break-words">{title}</SheetTitle>
          <SheetDescription className="text-xs">{subtitle}</SheetDescription>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {nav.position && <span className="mr-1 text-xs text-muted-foreground tabular-nums">{nav.position}</span>}
          <Button size="icon-sm" variant="outline" aria-label="Previous record" title="Previous record (K)" aria-keyshortcuts="K" disabled={!nav.prevId} onClick={() => nav.prevId && nav.onNav(nav.prevId)}>
            <ChevronUp className="h-4 w-4 shrink-0" />
          </Button>
          <Button size="icon-sm" variant="outline" aria-label="Next record" title="Next record (J)" aria-keyshortcuts="J" disabled={!nav.nextId} onClick={() => nav.nextId && nav.onNav(nav.nextId)}>
            <ChevronDown className="h-4 w-4 shrink-0" />
          </Button>
          <SheetClose render={<Button size="icon-sm" variant="ghost" aria-label="Close record" />}>
            <X className="h-4 w-4 shrink-0" />
          </SheetClose>
        </div>
      </div>
      {children}
    </div>
  );
}

function Kbd({ children }: { children: React.ReactNode }) {
  return <kbd aria-hidden className="ml-1 hidden rounded border border-current/30 px-1 font-mono text-[10px] leading-4 opacity-70 sm:inline">{children}</kbd>;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <h3 className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">{title}</h3>
      {children}
    </section>
  );
}

function Text({ id, label, value, onChange }: { id: string; label: string; value: string; onChange: (v: string) => void }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <Label htmlFor={id} className="text-xs text-muted-foreground">{label}</Label>
      <Input id={id} value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function DetailForm({ c, onDone, nav }: { c: Candidate; onDone: (next: number | null) => void; nav: Nav }) {
  const qc = useQueryClient();
  const f = c.fields;
  const fv = <K extends keyof CandidateFields>(k: K) =>
    f[k]?.value as (NonNullable<CandidateFields[K]> extends Field<infer T> ? T : never) | undefined;
  // This form renders only in the browser (after the record loads), so storage is readable here.
  const [stored] = useState(() => loadDraft(c));
  const [restored, setRestored] = useState<string | null>(stored?.saved_at ?? null);
  const [draft, setDraft] = useState<Draft>(() => stored?.draft ?? initialDraft(c));
  const [source, setSource] = useState<number | null>(null);
  const [mergeId, setMergeId] = useState("");

  const set = (patch: Partial<Draft>) => setDraft((d) => {
    const next = { ...d, ...patch };
    saveDraft(c, next);
    return next;
  });
  const discard = () => { clearDraft(c.id); setDraft(initialDraft(c)); setRestored(null); };

  // Approve, reject and merge move on to the next record so the admin keeps working through the list.
  const finish = (msg: string, advance = true) => {
    clearDraft(c.id);
    toast.success(msg);
    qc.invalidateQueries({ queryKey: ["candidates"] });
    qc.invalidateQueries({ queryKey: keys.status });
    qc.removeQueries({ queryKey: keys.candidate(c.id) });
    onDone(advance ? nav.nextId : null);
  };
  const fail = (e: Error) => toast.error(e.message);
  const approve = useMutation({
    mutationFn: () => post(`/api/candidates/${c.id}/approve`, toClientIn(draft, c)),
    onSuccess: () => finish("Approved. It stays in staging until you publish."), onError: fail,
  });
  const action = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown; msg: string; advance?: boolean }) => post(`/api/candidates/${c.id}/${path}`, body),
    onSuccess: (_r, v) => finish(v.msg, v.advance ?? true), onError: fail,
  });
  const busy = approve.isPending || action.isPending;

  // Approve lives in the pinned footer, outside the form (the Details tab may be closed), so it calls this directly.
  const submit = (e?: React.FormEvent) => {
    e?.preventDefault();
    const problem = validate(draft);
    if (problem) return toast.error(problem);
    approve.mutate();
  };

  const amounts = (c.reviewed?.amounts ?? fv("amounts")) ?? [];
  const dates = fv("stage_dates") ?? {};
  const timeline = fv("timeline") ?? { items: [], truncated: false };
  const lastContact = (c.reviewed?.last_contact ?? fv("last_contact")) ?? null;

  const reject = () => action.mutate({ path: "reject", msg: "Rejected." });
  // Keyboard shortcuts; re-bound every render so they always see the current draft and nav.
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.repeat || e.ctrlKey || e.metaKey || e.altKey || busy || source) return;
      const t = e.target as HTMLElement | null;
      if (t?.closest("input, textarea, select, [contenteditable=true], [role=combobox], [role=listbox], [role=menu]")) return;
      const k = e.key.toLowerCase();
      if (k === "a") submit();
      else if (k === "r") reject();
      else if (k === "j" && nav.nextId) nav.onNav(nav.nextId);
      else if (k === "k" && nav.prevId) nav.onNav(nav.prevId);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  });

  const stateTone = { pending: "warning", approved: "success", rejected: "danger", merged: "neutral" } as const;
  return (
    <div className="flex h-full min-h-0 flex-col">
      <DrawerHeader title={draft.name || f.name?.value || c.email_key} nav={nav}
        subtitle={c.published_at ? `In official table (client ${c.client_id}), published ${stamp(c.published_at)}`
          : c.reviewed_at ? `Approved in staging ${stamp(c.reviewed_at)}, not yet published` : "Not in official table"}>
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <Pill tone="neutral" className="tabular-nums">#{c.id}</Pill>
          <Pill tone={stateTone[c.review_state]} className="capitalize">{c.review_state === "approved" ? "confirmed" : c.review_state}</Pill>
          {c.merged_into && <Pill>merged into #{c.merged_into}</Pill>}
          <StageBadge stage={draft.stage} />
          <span className="truncate text-muted-foreground">{c.email_key}</span>
        </div>
      </DrawerHeader>
      {c.review_state === "approved" && (
        <div role="status" className="flex items-center gap-2 border-b border-success/30 bg-success/10 px-5 py-2 text-sm font-medium text-success">
          <CheckCircle2 aria-hidden className="h-4 w-4 shrink-0" />
          {c.published_at ? `Confirmed client · published ${stamp(c.published_at)}` : "Confirmed client · in staging, not yet published"}
        </div>
      )}

      <Tabs defaultValue="details" className="min-h-0 flex-1 gap-0">
        <div className="border-b px-5 py-2">
          <TabsList variant="line" aria-label="Record sections">
            <TabsTrigger value="details">Details</TabsTrigger>
            <TabsTrigger value="timeline">Timeline <span className="text-xs text-muted-foreground tabular-nums">{timeline.items.length}</span></TabsTrigger>
            <TabsTrigger value="evidence">Evidence <span className="text-xs text-muted-foreground tabular-nums">{c.evidence.length}</span></TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="details" className="min-h-0 overflow-y-auto px-5 py-4">
          <div className="flex flex-col gap-6">
            {restored && (
              <div role="status" className="flex flex-wrap items-center gap-2 rounded-lg border border-info/30 bg-info/10 px-3 py-2 text-sm">
                Restored unsaved edits from {new Date(restored).toLocaleString()}.
                <Button type="button" variant="ghost" size="sm" onClick={discard}>Discard edits</Button>
              </div>
            )}

            <form id="record-form" onSubmit={submit} className="flex flex-col gap-6">
              <Section title="Contact">
                <Text id="d_name" label="Name" value={draft.name} onChange={(v) => set({ name: v })} />
                <div className="grid gap-3 sm:grid-cols-2">
                  <Text id="d_emails" label="Emails (comma list)" value={draft.emails} onChange={(v) => set({ emails: v })} />
                  <Text id="d_phones" label="Phones (comma list)" value={draft.phones} onChange={(v) => set({ phones: v })} />
                  <Text id="d_company" label="Company" value={draft.company} onChange={(v) => set({ company: v })} />
                  <Text id="d_department" label="Department handling" value={draft.department} onChange={(v) => set({ department: v })} />
                </div>
              </Section>
              <Section title="Engagement">
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="flex flex-col gap-1">
                    <Label htmlFor="d_stage" className="text-xs text-muted-foreground">Stage</Label>
                    <Pick id="d_stage" label="Stage" value={draft.stage} options={STAGES.map((s) => ({ value: s, label: s }))}
                      onChange={(v) => set({ stage: v as Draft["stage"] })} />
                  </div>
                  <div className="flex flex-col gap-1">
                    <Label htmlFor="d_status" className="text-xs text-muted-foreground">Status</Label>
                    <Pick id="d_status" label="Status" value={draft.status} options={STATUSES.map((s) => ({ value: s, label: s }))}
                      onChange={(v) => set({ status: v as Draft["status"] })} />
                  </div>
                </div>
                <Label className="gap-2 font-normal"><Checkbox checked={draft.paid} onCheckedChange={(v) => set({ paid: !!v })} /> Engagement paid</Label>
                <Text id="d_services" label="Services mentioned (comma list)" value={draft.services} onChange={(v) => set({ services: v })} />
                <div className="flex flex-col gap-1">
                  <Label htmlFor="d_notes" className="text-xs text-muted-foreground">Notes</Label>
                  <Textarea id="d_notes" rows={2} value={draft.notes} onChange={(e) => set({ notes: e.target.value })} />
                </div>
              </Section>

              <Section title="Client details">
                <ClientDetails draft={draft} set={set} mentioned={fv("services") ?? []} handlers={(c.reviewed?.handlers ?? fv("handlers")) ?? []}
                  lastContact={lastContact} onSource={setSource} />
              </Section>

              <Section title="Amounts and stage dates">
                {amounts.length ? (
                  <ul className="divide-y rounded-lg border text-sm">
                    {amounts.map((a, i) => (
                      <li key={i} className="flex flex-wrap items-center gap-2 px-3 py-2">
                        <StageBadge stage={a.stage} /> <span className="font-medium tabular-nums">{money(a)}</span>
                        <span className="ml-auto text-xs text-muted-foreground tabular-nums">{day(a.date) || "undated"}</span>
                      </li>
                    ))}
                  </ul>
                ) : <p className="text-sm text-muted-foreground">No quoted or invoiced amount found.</p>}
                {STAGES.some((k) => dates[k]) && (
                  <p className="text-xs text-muted-foreground">{STAGES.filter((k) => dates[k]).map((k) => `${k}: ${day(dates[k])}`).join(" · ")}</p>
                )}
              </Section>

              <p className="border-t pt-3 text-xs text-muted-foreground">
                Found from: {(["name", "company", "stage", "status", "department"] as const).filter((k) => f[k])
                  .map((k) => `${k} (${f[k]!.source}, ${Math.round(f[k]!.confidence * 100)}%)`).join(" · ")}
                {fv("sources") ? ` · sources: ${fv("sources")!.join(", ")}` : ""}
              </p>
            </form>
          </div>
        </TabsContent>

        <TabsContent value="timeline" className="min-h-0 overflow-y-auto px-5 py-4">
          {timeline.truncated && <p className="mb-2 text-xs text-muted-foreground">Showing the latest {timeline.items.length} deal emails; older ones are not listed.</p>}
          {timeline.items.length === 0 && <p className="py-8 text-center text-sm text-muted-foreground">No deal emails yet.</p>}
          <ol className="relative flex flex-col border-l pl-5">
            {timeline.items.map((t) => (
              <li key={t.raw_item_id + t.stage} className="relative flex flex-col gap-1 pb-5">
                <span aria-hidden className="absolute top-1.5 -left-[25px] h-2.5 w-2.5 rounded-full border-2 border-card bg-primary" />
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <span className="font-medium text-foreground tabular-nums">{day(t.date) || "undated"}</span>
                  <StageBadge stage={t.stage} evidence />
                  {t.amounts.length > 0 && <span className="font-medium text-foreground tabular-nums">{t.amounts.map(money).join(", ")}</span>}
                  <Button type="button" variant="ghost" size="xs" className="ml-auto" onClick={() => setSource(t.raw_item_id)}>
                    <Mail className="h-3 w-3 shrink-0" /> View email
                  </Button>
                </div>
                <span className="text-xs text-muted-foreground">{t.department || "No department"} · {t.mailbox || "—"}</span>
                <p className="text-sm break-words">{t.snippet}</p>
              </li>
            ))}
          </ol>
        </TabsContent>

        <TabsContent value="evidence" className="min-h-0 overflow-y-auto px-5 py-4">
          {c.evidence.length === 0 && <p className="py-8 text-center text-sm text-muted-foreground">No evidence stored.</p>}
          <div className="flex flex-col gap-2">
            {c.evidence.map((e, i) => (
              <div key={i} className="flex flex-col gap-1.5 rounded-lg border p-3 text-xs">
                <div className="flex flex-wrap items-center gap-2 text-muted-foreground">
                  {e.stage && <StageBadge stage={e.stage} evidence />}
                  <span>
                    {day(e.date) || "undated"} · {e.source} · {e.direction === "out" ? "sent by staff" : "received"}
                    {e.amounts?.length ? ` · ${e.amounts.map(money).join(", ")}` : ""}
                    {e.services?.length ? ` · ${e.services.join(", ")}` : ""}
                  </span>
                  <Button type="button" variant="ghost" size="xs" className="ml-auto" onClick={() => setSource(e.raw_item_id)}>
                    <Mail className="h-3 w-3 shrink-0" /> View email
                  </Button>
                </div>
                {e.reasons?.length > 0 && <p className="text-muted-foreground">Why: {e.reasons.join("; ")}</p>}
                <p className="text-sm break-words whitespace-pre-wrap">{e.snippet}</p>
              </div>
            ))}
          </div>
        </TabsContent>
      </Tabs>

      <div className="flex flex-wrap items-center gap-2 border-t bg-card px-5 py-3">
        <Button type="button" disabled={busy} onClick={() => submit()} aria-keyshortcuts="A">
          <CheckCircle2 className="h-4 w-4 shrink-0" />{approve.isPending ? "Saving…" : "Confirm client"}<Kbd>A</Kbd>
        </Button>
        <Button type="button" variant="destructive" disabled={busy} onClick={reject} aria-keyshortcuts="R">Reject<Kbd>R</Kbd></Button>
        <Button type="button" variant="outline" disabled={busy}
          onClick={() => action.mutate({ path: "reopen", msg: "Moved back to pending.", advance: false })}>Back to pending</Button>
        <div className="ml-auto flex items-center gap-1">
          <Input type="number" min="1" aria-label="Merge into candidate ID" placeholder="Merge into ID" className="w-32"
            value={mergeId} onChange={(e) => setMergeId(e.target.value)} />
          <Button type="button" variant="outline" disabled={busy || !Number(mergeId)}
            onClick={() => action.mutate({ path: "merge", body: { into_id: Number(mergeId) }, msg: "Merged." })}>Merge</Button>
        </div>
      </div>

      {source && <SourceDialog id={source} onClose={() => setSource(null)} />}
    </div>
  );
}
