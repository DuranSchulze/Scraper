# DESIGN.md — Metric Slate Gold

Design system for **Workspace Scraper**, an internal tool for reviewing scraped client records
before they are published. This file is the source of truth for visual and interaction decisions.
Tokens live in [frontend/src/app/globals.css](frontend/src/app/globals.css); components live in
[frontend/src/components/ui/](frontend/src/components/ui/).

## 1. Principles

1. **Data first.** The record, its status and the next action are the page. Chrome stays quiet.
2. **One accent.** Gold (`--primary`) marks the main action and the selected state, nothing else.
3. **Tone, not lines.** Separate regions with surface tiers, spacing and type weight, not rules.
   Exceptions the user asked for: buttons and KPI cards keep a visible border.
4. **Tint status, never fill it.** Status uses a ~16% tint of its colour with coloured text.
5. **Dense but calm.** Small controls (h-8), compact tables, generous gaps between groups.
6. **Both themes are first class.** Dark is the default; light must be equally readable.
7. **Motion is feedback.** Short, small, and disabled under `prefers-reduced-motion`.

## 2. Stack

| Concern | Choice |
|---|---|
| Framework | Next.js 16 App Router, React 19 |
| Styling | Tailwind CSS v4 (`@theme inline`), `tw-animate-css` |
| Primitives | shadcn on `@base-ui/react`, variants via `class-variance-authority` |
| Icons | `lucide-react` (SVG only; never emoji or Unicode glyphs) |
| Toasts | `sonner` (`components/ui/sonner.tsx`) |
| Dates | `date-fns`, `react-day-picker` |
| Data | `@tanstack/react-query` against the backend API |

Add no UI dependency without a plan that asks for it.

## 3. Colour tokens

All colours are CSS variables on `:root` (dark) and `html[data-theme="light"]`. Use the Tailwind
name (`bg-card`, `text-muted-foreground`), never a raw hex in a component.

### Surfaces (tonal tiers, darkest to lightest in dark mode)

| Token | Dark | Light | Use |
|---|---|---|---|
| `--sidebar` | `#12171c` | `#ffffff` | Navigation rail |
| `--field` | `#151a1f` | `#ffffff` | Input wells |
| `--background` | `#1a1f24` | `#f4f6f8` | Page |
| `--card` | `#232930` | `#ffffff` | Cards, tables, panels |
| `--secondary` / `--muted` | `#293138` | `#eceff3` | Quiet fills, chips, hover |
| `--accent` / `--popover` | `#2d353e` | `#e6eaef` / `#ffffff` | Selected rows, menus |

Raise an element one tier to separate it. Do not add a border instead.

### Text

| Token | Dark | Light |
|---|---|---|
| `--foreground` | `#dee3ea` | `#1a1f24` |
| `--muted-foreground` | `#aeb8c4` | `#5a6572` |

### Accent and semantic

| Token | Dark | Light | Rule |
|---|---|---|---|
| `--primary` | `#ffc513` | `#ffc513` | Fill for main button, active tab, selected pill |
| `--primary-foreground` | `#1a1f24` | `#1a1f24` | Text on gold |
| `--primary-text` | `#ffc513` | `#8a6500` | Gold **as text**; darker in light mode for contrast |
| `--ring` | `#ffc513` | `#b58500` | Focus ring |
| `--success` | `#6fd3a6` | `#146c45` | Approved, synced, done |
| `--warning` | `#ffc513` | `#8a5a00` | Needs attention |
| `--info` | `#8db4ff` | `#1f55b8` | Neutral information, running |
| `--destructive` | `#ffb4ab` | `#c62828` | Errors, delete, reject |
| `--notify` | `#ef4444` | `#dc2626` | Count dots only |
| `--border` | `#4b5663` | `#dfe4ea` | Only where a line is required |
| `--input` | `#76828f` | `#c5ccd5` | Control outlines |

Never use `text-primary` for text. Use `text-primary-text`.

### Charts

`--chart-1` is gold; `--chart-2..5` step down through slate. Gold is the series that matters;
the rest is context. Follow the dataviz skill for new charts.

## 4. Status pattern

```tsx
// Tinted status pill: ~16% tint, coloured text, no border, no solid fill.
<span className="rounded-full bg-[color-mix(in_oklab,var(--success)_16%,transparent)] px-2 py-0.5 text-xs font-medium text-success">
  Approved
</span>
```

| Domain value | Token |
|---|---|
| Stage `inquiry` | `--info` |
| Stage `quoted` | `--warning` |
| Stage `invoiced` | `--primary-text` |
| Stage `paid` | `--success` |
| Status `active` | `--success` |
| Status `past` | `--muted-foreground` |
| Status `lost` | `--destructive` |
| Run failed / error | `--destructive` |

Stages and statuses come from `STAGES` and `STATUSES` in
[frontend/src/lib/types.ts](frontend/src/lib/types.ts). Keep labels in sync with them.
A selected filter pill uses the same 16% gold tint with `text-primary-text` (`PILL_ON` in
`review-app.tsx`).

## 5. Typography

| Role | Font | Size / weight |
|---|---|---|
| UI and headings | Manrope (`--font-sans`) | 400, 500, 600, 700 loaded |
| Numbers, IDs, logs | Geist Mono (`--font-mono`) | `font-mono tabular-nums` |
| Page title | Manrope | `text-xl font-semibold tracking-tight` |
| Section / card title | Manrope | `text-base font-medium` |
| Body, table cells | Manrope | `text-sm` |
| Meta, labels, badges | Manrope | `text-xs font-medium`, muted colour |

Hierarchy comes from weight and colour before size. Use sentence case everywhere.

## 6. Spacing, radius and elevation

- **Spacing:** Tailwind 4px scale. Inside controls `gap-1.5`; inside cards `--card-spacing`
  (`4` default, `3` for `size="sm"`); between page sections `gap-6`.
- **Radius:** base `--radius: 0.5rem`. Controls `rounded-lg`; cards `rounded-2xl`;
  pills, chips and tab indicators `rounded-full`.
- **Elevation:** surface tier first. Shadows only on floating layers (popover, dialog, sheet,
  sliding tab indicator `shadow-sm`). Cards use `ring-1 ring-foreground/10`, not a shadow.

## 7. Layout

- **Shell:** [app-shell.tsx](frontend/src/components/shell/app-shell.tsx). Fixed sidebar
  16rem, collapses to 4rem on `lg+` via `html[data-sidebar]`. Icons sit in a fixed 3rem slot so
  they never move while the width animates. Mobile uses a drawer, always expanded.
- **Navigation groups:** Workspace (Review `/`, Scraping `/scraping`, Logs `/logs`) and
  Admin (Accounts `/accounts`). Active row sets `aria-current="page"`.
- **Page header:** [page-header.tsx](frontend/src/components/shell/page-header.tsx). Title,
  optional one-line description, actions on the right; wraps on narrow screens.
- **Loading:** `PageSkeleton` and `Skeleton` match final layout to avoid shifts.
- **Breakpoints:** Tailwind defaults. Design at 1440 and 390 widths. No horizontal page scroll
  at any width; wide tables scroll inside their own container with a sticky first column.
- **Pre-paint state:** an inline script in `layout.tsx` sets `data-theme` and `data-sidebar`
  from `localStorage` (`scraper.theme`, `scraper.sidebar.collapsed`) so nothing flashes.

## 8. Components

| Component | File | Notes |
|---|---|---|
| Button | `ui/button.tsx` | Variants `default` (gold, main action), `outline`, `secondary`, `ghost`, `destructive` (tinted), `link`. Every variant except `link` has a border (user request). Sizes `xs`–`lg`, `icon*`. Default height h-8. |
| Badge | `ui/badge.tsx` | For counts and tags. For status, use the tinted pattern in §4. |
| Card | `ui/card.tsx` | `rounded-2xl`, ring, `size="sm"` for dense panels. KPI cards keep a border. |
| Table / RecordsTable | `ui/table.tsx`, `review/records-table.tsx` | Sticky pinned first column; hover tints with `--muted` at 40%; selected row uses `--accent`. |
| Pager | `ui/table-pager.tsx` | Shared Client Directory pager, used by Review and Logs. Page sizes from `PAGE_SIZES`. |
| Tabs | `ui/tabs.tsx` | Pill tabs with a sliding gold indicator (300 ms). |
| Dialog / ConfirmDialog | `ui/dialog.tsx`, `review/confirm-dialog.tsx` | Confirm every destructive or publishing action. |
| Sheet | `ui/sheet.tsx` | Record detail and mobile nav. |
| Popover, Select, Calendar | `ui/*` | Date range filter is `review/date-range-filter.tsx`. |
| Input, Textarea | `ui/*` | Sit on `--field`; outline `--input`. |
| Switch, Checkbox | `ui/*` | Gold when on. |
| Progress / StatusBar | `ui/progress.tsx`, `scrape/status-bar.tsx` | Unknown totals use the `stage-sweep` animation. |
| Toaster | `ui/sonner.tsx` | Short past-tense confirmations ("Record approved"). |
| Tooltip | `ui/tooltip.tsx` | Required on icon-only buttons and collapsed sidebar rows. |

Before writing a new component, check this table and `components/` for one to reuse.

## 9. Interaction and motion

- **Hover lift:** `motion-safe:hover:-translate-y-0.5` on cards and chips.
- **Press:** `active:translate-y-px` on buttons; `active:scale-[0.97]` on chips.
- **Indicators:** tab and segment indicators slide with `duration-300 ease-out`.
- **Sidebar:** width `0.2s cubic-bezier(0.2, 0, 0, 1)`; labels fade `0.15s`.
- **Theme switch:** instant; no page-wide colour transition.
- **Reduced motion:** every transition and transform has a `motion-reduce:` off switch.
- **Cursor:** pointer on all clickable roles, `not-allowed` on disabled (see `globals.css`).

## 10. States

Every data view designs all of these:

| State | Treatment |
|---|---|
| Loading | Skeleton matching final layout |
| Empty | One sentence on why it is empty plus the next action |
| Error | Tinted destructive block, plain message, retry button |
| Partial / running | StatusBar with stage and counts; live LogPanel |
| No access | `/no-access` page; RouteGuard hides routes the role cannot use |
| Disabled | 50% opacity, `not-allowed` cursor, tooltip saying why when not obvious |

## 11. Accessibility

- Contrast: WCAG AA for text in both themes. Gold text in light mode must use `--primary-text`.
- Focus: visible `ring-3 ring-ring/50` on every interactive element. Never remove it.
- Labels: every control has a visible `Label` or an `aria-label`; icon buttons need both
  `aria-label` and a tooltip.
- Landmarks: `nav aria-label="Main"`, one `h1` per page via `PageHeader`.
- Status is never colour-only: pills always carry a text label.
- Keyboard: dialogs trap focus and close on Escape; tables support row selection by keyboard.

## 12. Copy

- Plain, short, sentence case. Verbs on buttons: "Approve", "Run scrape", "Export".
- Name things the same everywhere: record, client, mailbox, run, stage, status.
- Dates use the helpers in `lib/` (Philippine time, `PH`); show relative time only with the
  absolute value in a tooltip.
- No exclamation marks, no filler ("Successfully…"), no emoji.

## 13. Print

The Logs report prints only page content: sidebar and `[data-print="hide"]` are hidden, and
every Logs tab's table prints, not only the open one.

## 14. Definition of done for UI changes

1. Uses tokens and existing components only; no raw hex, no new dependency.
2. No separator lines added (except buttons and KPI cards); status is tinted.
3. Icons are lucide SVGs.
4. Hover and press motion present and disabled under reduced motion.
5. `scripts/copy-lint.mjs` from taste-skill passes on changed pages.
6. Checked in dark and light themes at 1440 and 390 widths with no sideways scroll,
   no console errors and no unlabeled controls.
7. `npm run lint` and `npm run typecheck` pass in `frontend/`.
