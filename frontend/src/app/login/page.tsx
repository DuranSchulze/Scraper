import { Suspense } from "react";
import { ClipboardCheck, Download, ScanSearch, Upload } from "lucide-react";
import { LoginForm } from "@/components/auth/login-form";

export const metadata = { title: "Sign in · Workspace Scraper" };

const STEPS = [
  { icon: Download, title: "Scrape", text: "Pull mail, contacts and sheets from your Workspace." },
  { icon: ClipboardCheck, title: "Review", text: "Check each client record and fix what the scraper got wrong." },
  { icon: Upload, title: "Publish", text: "Send approved records to the official clients table." },
];

export default function Page() {
  return (
    <main className="grid min-h-screen lg:grid-cols-[minmax(0,5fr)_minmax(0,4fr)]">
      <section className="relative flex flex-col justify-between gap-10 overflow-hidden bg-sidebar p-6 text-sidebar-foreground sm:p-10 lg:p-14">
        {/* Faint grid, fading out downward: texture without competing with the copy. */}
        <div aria-hidden className="pointer-events-none absolute inset-0 opacity-[0.07] [background-image:linear-gradient(currentColor_1px,transparent_1px),linear-gradient(90deg,currentColor_1px,transparent_1px)] [background-size:48px_48px] [mask-image:linear-gradient(to_bottom,black,transparent_85%)]" />
        <div className="relative flex items-center gap-3">
          <span className="flex size-9 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
            <ScanSearch className="h-[1.15rem] w-[1.15rem] shrink-0" />
          </span>
          <span className="text-[0.95rem] font-semibold tracking-tight">Workspace Scraper</span>
        </div>
        <div className="relative max-w-xl">
          <h2 className="text-3xl leading-tight font-bold tracking-tight text-balance text-sidebar-accent-foreground sm:text-4xl lg:text-[2.6rem]">
            Client records, checked by a person before they go live.
          </h2>
          <ol className="mt-8 hidden flex-col gap-5 sm:flex">
            {STEPS.map(({ icon: Icon, title, text }, i) => (
              <li key={title} className="flex items-start gap-4">
                <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-sidebar-accent text-sidebar-accent-foreground">
                  <Icon className="h-4 w-4 shrink-0" />
                </span>
                <span>
                  <span className="block text-sm font-semibold"><span className="text-sidebar-foreground/60 tabular-nums">{i + 1}.</span> {title}</span>
                  <span className="block text-sm text-sidebar-foreground/75">{text}</span>
                </span>
              </li>
            ))}
          </ol>
        </div>
        <p className="relative hidden text-xs text-sidebar-foreground/60 lg:block">Access is by invitation only.</p>
      </section>
      <section className="flex items-center justify-center p-6 sm:p-10">
        <Suspense fallback={null}>
          <LoginForm />
        </Suspense>
      </section>
    </main>
  );
}
