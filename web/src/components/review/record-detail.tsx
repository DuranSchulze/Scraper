"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { candidateQuery, keys, post } from "@/lib/api";
import { day, money, stamp } from "@/lib/format";
import { STAGES, STATUSES, type Candidate, type CandidateFields, type Field } from "@/lib/types";
import { StageBadge } from "./badges";
import { ClientDetails } from "./client-details";
import { clearDraft, initialDraft, loadDraft, saveDraft, toClientIn, validate, type Draft } from "./draft";
import { Pick } from "./pick";
import { SourceDialog } from "./text-dialog";

export function RecordDetail({ id, onDone }: { id: number; onDone: () => void }) {
  const { data, error } = useQuery(candidateQuery(id));
  if (error) return <p role="alert" className="text-sm text-destructive">Could not load record #{id}: {error.message}</p>;
  if (!data) return <p className="py-8 text-center text-sm text-muted-foreground">Loading record #{id}…</p>;
  return <DetailForm key={`${data.id}:${data.email_key}`} c={data} onDone={onDone} />;
}

function Text({ id, label, value, onChange }: { id: string; label: string; value: string; onChange: (v: string) => void }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <Label htmlFor={id} className="text-xs text-muted-foreground">{label}</Label>
      <Input id={id} value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function DetailForm({ c, onDone }: { c: Candidate; onDone: () => void }) {
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

  const finish = (msg: string) => {
    clearDraft(c.id);
    toast.success(msg);
    qc.invalidateQueries({ queryKey: ["candidates"] });
    qc.invalidateQueries({ queryKey: keys.status });
    qc.removeQueries({ queryKey: keys.candidate(c.id) });
    onDone();
  };
  const fail = (e: Error) => toast.error(e.message);
  const approve = useMutation({
    mutationFn: () => post(`/api/candidates/${c.id}/approve`, toClientIn(draft, c)),
    onSuccess: () => finish("Approved. It stays in staging until you publish."), onError: fail,
  });
  const action = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown; msg: string }) => post(`/api/candidates/${c.id}/${path}`, body),
    onSuccess: (_r, v) => finish(v.msg), onError: fail,
  });
  const busy = approve.isPending || action.isPending;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const problem = validate(draft);
    if (problem) return toast.error(problem);
    approve.mutate();
  };

  const checks = fv("needs_check") ?? [];
  const amounts = (c.reviewed?.amounts ?? fv("amounts")) ?? [];
  const dates = fv("stage_dates") ?? {};
  const timeline = fv("timeline") ?? { items: [], truncated: false };
  const lastContact = (c.reviewed?.last_contact ?? fv("last_contact")) ?? null;

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-lg font-semibold tracking-tight break-all">
          #{c.id} · {c.review_state}{c.merged_into ? ` · merged into ${c.merged_into}` : ""}
        </h2>
        <p className="text-xs text-muted-foreground">
          {c.published_at ? `In official table (client ${c.client_id}), published ${stamp(c.published_at)}`
            : c.reviewed_at ? `Approved in staging ${stamp(c.reviewed_at)}, not yet published` : "Not in official table"}
        </p>
      </div>

      {restored && (
        <div role="status" className="flex flex-wrap items-center gap-2 rounded-lg border border-info/30 bg-info/10 px-3 py-2 text-sm">
          Restored unsaved edits from {new Date(restored).toLocaleString()}.
          <Button type="button" variant="ghost" size="sm" onClick={discard}>Discard edits</Button>
        </div>
      )}
      {checks.length > 0 && (
        <div role="alert" className="rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-sm">
          <strong>Check before approving</strong>
          <ul className="mt-1 list-disc pl-5">{checks.map((t) => <li key={t}>{t}</li>)}</ul>
        </div>
      )}

      <form onSubmit={submit} className="flex flex-col gap-3">
        <Text id="d_name" label="Name" value={draft.name} onChange={(v) => set({ name: v })} />
        <div className="grid gap-3 sm:grid-cols-2">
          <Text id="d_emails" label="Emails (comma list)" value={draft.emails} onChange={(v) => set({ emails: v })} />
          <Text id="d_phones" label="Phones (comma list)" value={draft.phones} onChange={(v) => set({ phones: v })} />
          <Text id="d_company" label="Company" value={draft.company} onChange={(v) => set({ company: v })} />
          <Text id="d_department" label="Department handling" value={draft.department} onChange={(v) => set({ department: v })} />
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

        <h3 className="mt-2 text-base font-medium text-muted-foreground">Client details</h3>
        <ClientDetails draft={draft} set={set} mentioned={fv("services") ?? []} handlers={(c.reviewed?.handlers ?? fv("handlers")) ?? []}
          lastContact={lastContact} onSource={setSource} />

        <h3 className="mt-2 text-base font-medium text-muted-foreground">Amounts and stage dates</h3>
        {amounts.length ? (
          <ul className="flex flex-col gap-1 text-sm">
            {amounts.map((a, i) => (
              <li key={i} className="flex flex-wrap items-center gap-2">
                <StageBadge stage={a.stage} /> <span className="tabular-nums">{money(a)}</span>
                <span className="text-muted-foreground tabular-nums">{day(a.date) || "undated"}</span>
              </li>
            ))}
          </ul>
        ) : <p className="text-sm text-muted-foreground">No quoted or invoiced amount found.</p>}
        <p className="text-xs text-muted-foreground">
          {STAGES.filter((k) => dates[k]).map((k) => `${k}: ${day(dates[k])}`).join(" · ")}
        </p>
        <p className="text-xs text-muted-foreground">
          Found from: {(["name", "company", "stage", "status", "department"] as const).filter((k) => f[k])
            .map((k) => `${k} (${f[k]!.source}, ${Math.round(f[k]!.confidence * 100)}%)`).join(" · ")}
          {fv("sources") ? ` · sources: ${fv("sources")!.join(", ")}` : ""}
        </p>

        <div className="flex flex-wrap items-center gap-2 border-t pt-3">
          <Button type="submit" disabled={busy}>{approve.isPending ? "Saving…" : "Approve (staging)"}</Button>
          <Button type="button" variant="destructive" disabled={busy}
            onClick={() => action.mutate({ path: "reject", msg: "Rejected." })}>Reject</Button>
          <Button type="button" variant="outline" disabled={busy}
            onClick={() => action.mutate({ path: "reopen", msg: "Moved back to pending." })}>Back to pending</Button>
          <Input type="number" min="1" aria-label="Merge into candidate ID" placeholder="Merge into ID" className="w-36"
            value={mergeId} onChange={(e) => setMergeId(e.target.value)} />
          <Button type="button" variant="outline" disabled={busy || !Number(mergeId)}
            onClick={() => action.mutate({ path: "merge", body: { into_id: Number(mergeId) }, msg: "Merged." })}>Merge</Button>
        </div>
      </form>

      <section aria-labelledby="tlHead" className="flex flex-col gap-1">
        <h3 id="tlHead" className="text-base font-medium text-muted-foreground">Timeline</h3>
        {timeline.truncated && <p className="text-xs text-muted-foreground">Showing the latest {timeline.items.length} deal emails; older ones are not listed.</p>}
        {timeline.items.length === 0 && <p className="text-sm text-muted-foreground">No deal emails yet.</p>}
        {timeline.items.map((t) => (
          <div key={t.raw_item_id + t.stage} className="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-x-3 gap-y-1 border-t py-2 text-sm">
            <div className="flex flex-col items-start gap-1">
              <span className="text-xs tabular-nums">{day(t.date) || "undated"}</span>
              <StageBadge stage={t.stage} evidence />
            </div>
            <div className="flex min-w-0 flex-col gap-1">
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span>{t.department || "No department"} · {t.mailbox || "—"}</span>
                {t.amounts.length > 0 && <span className="tabular-nums">{t.amounts.map(money).join(", ")}</span>}
                <Button type="button" variant="ghost" size="xs" onClick={() => setSource(t.raw_item_id)}>
                  <ExternalLink className="h-3 w-3 shrink-0" /> Source
                </Button>
              </div>
              <p className="break-words">{t.snippet}</p>
            </div>
          </div>
        ))}
      </section>

      <section aria-labelledby="evHead" className="flex flex-col gap-1">
        <h3 id="evHead" className="text-base font-medium text-muted-foreground">Evidence</h3>
        {c.evidence.length === 0 && <p className="text-sm text-muted-foreground">No evidence stored.</p>}
        {c.evidence.map((e, i) => (
          <div key={i} className="flex flex-col gap-1 border-t py-2 text-xs">
            <div className="flex flex-wrap items-center gap-2 text-muted-foreground">
              {e.stage && <StageBadge stage={e.stage} evidence />}
              <span>
                {day(e.date) || "undated"} · {e.source} · {e.direction === "out" ? "sent by staff" : "received"}
                {e.amounts?.length ? ` · ${e.amounts.map(money).join(", ")}` : ""}
                {e.services?.length ? ` · ${e.services.join(", ")}` : ""}
              </span>
              <Button type="button" variant="ghost" size="xs" onClick={() => setSource(e.raw_item_id)}>
                <ExternalLink className="h-3 w-3 shrink-0" /> Open source
              </Button>
            </div>
            {e.reasons?.length > 0 && <p className="text-muted-foreground">Why: {e.reasons.join("; ")}</p>}
            <p className="text-sm break-words whitespace-pre-wrap">{e.snippet}</p>
          </div>
        ))}
      </section>

      {source && <SourceDialog id={source} onClose={() => setSource(null)} />}
    </div>
  );
}
