import { cn } from "@/lib/utils"

/** Placeholder block shaped like the content that is loading; hidden from screen readers (announce loading once, nearby). */
function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="skeleton" aria-hidden className={cn("animate-pulse rounded-md bg-muted motion-reduce:animate-none", className)} {...props} />
}

/** Screen-reader-only "Loading …" announcement to place next to a group of skeletons. */
function LoadingLabel({ children }: { children: React.ReactNode }) {
  return <span role="status" className="sr-only">{children}</span>
}

export { Skeleton, LoadingLabel }
