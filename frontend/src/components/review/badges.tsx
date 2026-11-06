import { cn } from "@/lib/utils";
import type { PaymentBasis } from "@/lib/types";

const TONE = {
  neutral: "bg-muted text-muted-foreground",
  info: "bg-info/15 text-info",
  warning: "bg-warning/20 text-foreground",
  success: "bg-success/15 text-success",
  danger: "bg-destructive/10 text-destructive",
} as const;
type Tone = keyof typeof TONE;

export function Pill({ tone = "neutral", className, children }: { tone?: Tone; className?: string; children: React.ReactNode }) {
  return <span className={cn("inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium", TONE[tone], className)}>{children}</span>;
}

const STAGE: Record<string, { label: string; tone: Tone }> = {
  inquiry: { label: "Inquiry", tone: "neutral" },
  quoted: { label: "Quoted", tone: "info" },
  invoiced: { label: "Invoiced", tone: "warning" },
  paid: { label: "Paid", tone: "success" },
  payment_claim: { label: "Client payment message", tone: "success" },
  payment_screenshot: { label: "Client image", tone: "success" },
  payment_confirmed: { label: "Payment confirmed", tone: "success" },
};
const EVIDENCE_LABEL: Record<string, string> = { quoted: "Quote / pricing sent", invoiced: "Invoice sent" };

export function StageBadge({ stage, evidence = false }: { stage: string; evidence?: boolean }) {
  const s = STAGE[stage] ?? { label: stage, tone: "neutral" as Tone };
  return <Pill tone={s.tone}>{(evidence && EVIDENCE_LABEL[stage]) || s.label}</Pill>;
}

export const BASIS: Record<PaymentBasis, { label: string; tone: Tone }> = {
  stated: { label: "Stated in payment", tone: "success" },
  invoice: { label: "From invoice — confirm", tone: "warning" },
  unknown: { label: "Amount unknown", tone: "warning" },
  manual: { label: "Entered by reviewer", tone: "neutral" },
};
