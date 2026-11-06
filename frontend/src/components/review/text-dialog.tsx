"use client";

import { useQuery } from "@tanstack/react-query";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { rawQuery, rulesQuery } from "@/lib/api";
import type { RawItem } from "@/lib/types";

function rawText(raw: RawItem): string {
  const p = raw.payload;
  if (raw.source !== "gmail") return JSON.stringify(p, null, 2);
  return Object.entries(p.headers ?? {}).map(([k, v]) => `${k}: ${v}`).join("\n")
    + ((p.attachments ?? []).length ? "\nattachments: " + p.attachments!.map((a) => a.filename).join(", ") : "")
    + "\n\n" + (p.body ?? "")
    + Object.entries(p.attachment_text ?? {}).map(([n, t]) => `\n\n--- ${n} ---\n${t}`).join("");
}

function Shell({ title, text, onClose }: { title: string; text: string; onClose: () => void }) {
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader><DialogTitle className="break-all pr-8">{title}</DialogTitle></DialogHeader>
        <pre className="max-h-[65vh] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-muted/50 p-3 font-mono text-xs">{text}</pre>
      </DialogContent>
    </Dialog>
  );
}

/** The original email (headers, body, attachments, PDF text) behind a piece of evidence. */
export function SourceDialog({ id, onClose }: { id: number; onClose: () => void }) {
  const { data, error } = useQuery(rawQuery(id));
  const title = data ? `${data.source} · ${data.mailbox || "—"} · ${data.source_id}` : `Source #${id}`;
  return <Shell title={title} text={error ? `Could not load: ${error.message}` : data ? rawText(data) : "Loading…"} onClose={onClose} />;
}

export function RulesDialog({ onClose }: { onClose: () => void }) {
  const { data, error } = useQuery(rulesQuery);
  return <Shell title={data ? `${data.path} (edit the file, then Re-run extraction)` : "Rules"}
    text={error ? `Could not load: ${error.message}` : data?.text ?? "Loading…"} onClose={onClose} />;
}
