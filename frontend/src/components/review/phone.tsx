import { parsePhoneNumberFromString } from "libphonenumber-js";
import * as Flags from "country-flag-icons/react/3x2";
import { Phone } from "lucide-react";
import { cn } from "@/lib/utils";

const REGION = new Intl.DisplayNames(["en"], { type: "region" });

/** Formatted number and its country. Numbers without a country code count as PH (the company's home). */
export function parsePhone(raw: string): { text: string; country?: string } {
  const p = parsePhoneNumberFromString(raw, "PH");
  return p?.isValid() ? { text: p.formatInternational(), country: p.country } : { text: raw };
}

export function PhoneNumber({ raw, className }: { raw: string; className?: string }) {
  const { text, country } = parsePhone(raw);
  const Flag = country ? Flags[country as keyof typeof Flags] : undefined;
  const name = country ? REGION.of(country) ?? country : "Unknown country";
  return (
    <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap tabular-nums", className)}>
      {Flag
        ? <Flag role="img" aria-label={name} className="h-3 w-[18px] shrink-0 rounded-[2px] shadow-[0_0_0_1px_rgb(0_0_0/0.08)]" />
        : <Phone aria-label={name} className="h-3.5 w-[18px] shrink-0 text-muted-foreground" />}
      <span title={name}>{text}</span>
    </span>
  );
}
