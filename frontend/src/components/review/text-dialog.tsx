"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Paperclip } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { rawQuery, rulesQuery } from "@/lib/api";
import type { RawItem } from "@/lib/types";
import { LoadingLabel, Skeleton } from "@/components/ui/skeleton";

const PRE = "max-h-[65vh] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-muted/50 p-3 font-mono text-xs";
const HEADERS = ["from", "to", "cc", "date"] as const;

/** Splits an email body into the new message and the quoted earlier thread ("On … wrote:" or "> " lines). */
export function splitQuoted(body: string): [string, string] {
  const lines = body.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    // Gmail wraps long "On <date> <name> wrote:" lines, so the marker may end on the next line.
    const wrote = /^On .+wrote:$/.test(line) || (/^On /.test(line) && /wrote:$/.test(lines[i + 1]?.trim() ?? ""));
    if (wrote || line.startsWith(">")) return [lines.slice(0, i).join("\n").trimEnd(), lines.slice(i).join("\n").trim()];
  }
  return [body.trimEnd(), ""];
}

function when(v: string) {
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? v : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function Fold({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <details className="group rounded-lg border">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 px-3 py-2 text-sm font-medium text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden">
        <ChevronRight aria-hidden className="h-4 w-4 shrink-0 transition-transform group-open:rotate-90" />{label}
      </summary>
      <pre className="max-h-80 overflow-auto border-t px-3 py-2 font-mono text-xs whitespace-pre-wrap break-words text-muted-foreground">{children}</pre>
    </details>
  );
}

/** A scraped Gmail message laid out like an email: headers, attachments, the new text, then folded history. */
function Email({ raw }: { raw: RawItem }) {
  const p = raw.payload;
  const h = Object.fromEntries(Object.entries(p.headers ?? {}).map(([k, v]) => [k.toLowerCase(), v]));
  const [message, quoted] = splitQuoted(p.body ?? "");
  const files = p.attachments ?? [];
  return (
    <>
      <DialogHeader>
        <DialogTitle className="pr-8 break-words">{h.subject || "(no subject)"}</DialogTitle>
        <DialogDescription className="text-xs">Email in {raw.mailbox || "unknown mailbox"} · id {raw.source_id}</DialogDescription>
      </DialogHeader>
      <div className="flex max-h-[70vh] min-w-0 flex-col gap-3 overflow-y-auto">
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 rounded-lg bg-muted/50 px-3 py-2 text-sm">
          {HEADERS.filter((k) => h[k]).map((k) => (
            <div key={k} className="contents">
              <dt className="text-muted-foreground capitalize">{k}</dt>
              <dd className="break-words">{k === "date" ? when(h[k]) : h[k]}</dd>
            </div>
          ))}
        </dl>
        {files.length > 0 && (
          <ul aria-label="Attachments" className="flex flex-wrap gap-1.5">
            {files.map((a, i) => (
              <li key={i} className="inline-flex max-w-full items-center gap-1 rounded-md border px-2 py-1 text-xs">
                <Paperclip aria-hidden className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <span className="truncate" title={a.filename}>{a.filename}</span>
              </li>
            ))}
          </ul>
        )}
        <div className="text-sm leading-relaxed break-words whitespace-pre-wrap">{message || <span className="text-muted-foreground">No new text in this email.</span>}</div>
        {quoted && <Fold label="Show earlier messages in this thread">{quoted}</Fold>}
        {Object.entries(p.attachment_text ?? {}).map(([name, text]) => <Fold key={name} label={`Text from ${name}`}>{text}</Fold>)}
      </div>
    </>
  );
}

function Shell({ title, children, onClose }: { title?: string; children: React.ReactNode; onClose: () => void }) {
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-3xl">
        {title && <DialogHeader><DialogTitle className="break-all pr-8">{title}</DialogTitle></DialogHeader>}
        {children}
      </DialogContent>
    </Dialog>
  );
}

/** The original email (headers, body, attachments, PDF text) behind a piece of evidence. */
export function SourceDialog({ id, onClose }: { id: number; onClose: () => void }) {
  const { data, error } = useQuery(rawQuery(id));
  if (data?.source === "gmail") return <Shell onClose={onClose}><Email raw={data} /></Shell>;
  const title = data ? `${data.source} · ${data.mailbox || "—"} · ${data.source_id}` : `Source #${id}`;
  return (
    <Shell title={title} onClose={onClose}>
      {!data && !error ? <TextSkeleton label="Loading source…" />
        : <pre className={PRE}>{error ? `Could not load: ${error.message}` : JSON.stringify(data!.payload, null, 2)}</pre>}
    </Shell>
  );
}

export function RulesDialog({ onClose }: { onClose: () => void }) {
  const { data, error } = useQuery(rulesQuery);
  return (
    <Shell title={data ? `${data.path} (edit the file, then Re-run extraction)` : "Rules"} onClose={onClose}>
      {!data && !error ? <TextSkeleton label="Loading rules…" />
        : <pre className={PRE}>{error ? `Could not load: ${error.message}` : data!.text}</pre>}
    </Shell>
  );
}

/** Lines of uneven width standing in for a text/JSON body while it loads. */
function TextSkeleton({ label }: { label: string }) {
  return (
    <div className="flex flex-col gap-2 rounded-md border bg-muted/30 p-3">
      <LoadingLabel>{label}</LoadingLabel>
      {[92, 70, 84, 60, 88, 45, 76, 66].map((w, i) => <Skeleton key={i} className="h-3.5" style={{ width: `${w}%` }} />)}
    </div>
  );
}
