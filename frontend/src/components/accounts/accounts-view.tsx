"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { LoadingLabel, Skeleton } from "@/components/ui/skeleton";
import { Pill } from "@/components/review/badges";
import { PageHeader } from "@/components/shell/page-header";
import { Pick } from "@/components/review/pick";
import { keys, meQuery, post, updateRoutes, usersQuery } from "@/lib/api";
import { stamp } from "@/lib/format";
import { ROUTES, type Account, type Role, type RouteKey } from "@/lib/types";

const ROLES = [{ value: "member", label: "Member" }, { value: "admin", label: "Admin" }];
const ROUTE_LABEL: Record<RouteKey, string> = { review: "Review", scraping: "Scraping" };
// Keeps the API's canonical order whatever order the boxes are ticked in.
const toggled = (routes: RouteKey[], r: RouteKey, on: boolean) => ROUTES.filter((k) => (k === r ? on : routes.includes(k)));

export function AccountsView() {
  const { data: me, isPending: meLoading } = useQuery(meQuery);
  const isAdmin = me?.role === "admin";
  const { data: users, error, isPending } = useQuery({ ...usersQuery, enabled: isAdmin });
  const [open, setOpen] = useState(false);

  if (!meLoading && !isAdmin) {
    return (
      <div className="flex flex-col gap-2">
        <PageHeader title="Accounts" />
        <p role="alert" className="py-8 text-center text-sm text-muted-foreground">Only administrators can manage accounts.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <PageHeader title="Accounts" description="People who can sign in and the pages each member may use. Admins always have every page."
        actions={<Button size="sm" onClick={() => setOpen(true)} disabled={!isAdmin}><Plus className="h-4 w-4 shrink-0" /> New account</Button>} />
      <Card className="gap-0 overflow-hidden p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Email</TableHead>
              <TableHead>Role</TableHead>
              <TableHead>Pages</TableHead>
              <TableHead>Created</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(users ?? []).map((u: Account) => (
              <TableRow key={u.id} className="hover:bg-muted/40">
                <TableCell className="font-medium">{u.name}</TableCell>
                <TableCell className="break-all">{u.email}</TableCell>
                <TableCell><Pill tone={u.role === "admin" ? "info" : "neutral"}>{u.role === "admin" ? "Admin" : "Member"}</Pill></TableCell>
                <TableCell>{u.role === "admin" ? <span className="text-muted-foreground">All pages</span> : <RouteToggles account={u} />}</TableCell>
                <TableCell className="tabular-nums text-muted-foreground">{stamp(u.created_at)}</TableCell>
              </TableRow>
            ))}
            {error && <TableRow><TableCell colSpan={5} role="alert" className="py-8 text-center text-destructive">{error.message}</TableCell></TableRow>}
            {!error && !isPending && !users?.length && <TableRow><TableCell colSpan={5} className="py-8 text-center text-muted-foreground">No accounts yet.</TableCell></TableRow>}
            {!error && isPending && (isAdmin || meLoading) && Array.from({ length: 3 }, (_, i) => (
              <TableRow key={`sk-${i}`} className="hover:bg-transparent">
                <TableCell>{i === 0 && <LoadingLabel>Loading accounts…</LoadingLabel>}<Skeleton className="h-4 w-28" /></TableCell>
                <TableCell><Skeleton className="h-4 w-44" /></TableCell>
                <TableCell><Skeleton className="h-5 w-16 rounded-full" /></TableCell>
                <TableCell><div className="flex gap-4"><Skeleton className="h-4 w-20" /><Skeleton className="h-4 w-20" /></div></TableCell>
                <TableCell><Skeleton className="h-4 w-28" /></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
      {/* Remounted per open so the draft always starts empty. */}
      {open && <NewAccountDialog onClose={() => setOpen(false)} />}
    </div>
  );
}

function NewAccountDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [routes, setRoutes] = useState<RouteKey[]>([...ROUTES]);
  const [error, setError] = useState("");
  const create = useMutation({
    mutationFn: () => post<Account>("/api/users", { name, email, password, role, ...(role === "member" ? { routes } : {}) }),
    onSuccess: (u) => { toast.success(`Account created for ${u.email}`); qc.invalidateQueries({ queryKey: keys.users }); onClose(); },
    onError: (e: Error) => setError(e.message),
  });
  const valid = name.trim() && email.trim() && password && (role === "admin" || routes.length > 0);

  return (
    <Dialog open onOpenChange={(o) => { if (!o && !create.isPending) onClose(); }}>
      <DialogContent>
        <form onSubmit={(e) => { e.preventDefault(); setError(""); create.mutate(); }} className="flex flex-col gap-4" noValidate>
          <DialogHeader>
            <DialogTitle>New account</DialogTitle>
            <DialogDescription>The person signs in with this email and password. Share the password securely.</DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="acc-name">Name</Label>
            <Input id="acc-name" autoComplete="off" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="acc-email">Email</Label>
            <Input id="acc-email" type="email" autoComplete="off" value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="acc-password">Password</Label>
            <Input id="acc-password" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="acc-role">Role</Label>
            <Pick id="acc-role" label="Role" value={role} options={ROLES} onChange={(v) => setRole(v as Role)} />
          </div>
          <fieldset className="flex flex-col gap-1.5">
            <legend className="mb-1.5 text-sm font-medium">Pages</legend>
            {role === "admin"
              ? <p className="text-sm text-muted-foreground">Admins can use every page, including Accounts.</p>
              : <div className="flex flex-wrap gap-4">
                  {ROUTES.map((r) => (
                    <label key={r} className="flex items-center gap-2 text-sm">
                      <Checkbox checked={routes.includes(r)} onCheckedChange={(on) => setRoutes((cur) => toggled(cur, r, on))} />
                      {ROUTE_LABEL[r]}
                    </label>
                  ))}
                </div>}
            {role === "member" && !routes.length && <p className="text-xs text-destructive">Pick at least one page.</p>}
          </fieldset>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={create.isPending}>Cancel</Button>
            <Button type="submit" disabled={!valid || create.isPending}>{create.isPending ? "Creating…" : "Create account"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Per-member page grants. Saves on each change, rolls back on failure, and never lets the last page be removed. */
function RouteToggles({ account }: { account: Account }) {
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: (routes: RouteKey[]) => updateRoutes(account.id, routes),
    onMutate: async (routes) => {
      await qc.cancelQueries({ queryKey: keys.users });
      // Snapshot only this account, so a failure never undoes another row's save that landed meanwhile.
      const before = account.routes;
      qc.setQueryData<Account[]>(keys.users, (list) => list?.map((u) => (u.id === account.id ? { ...u, routes } : u)));
      return { before };
    },
    onError: (e: Error, _r, ctx) => {
      if (ctx) qc.setQueryData<Account[]>(keys.users, (list) => list?.map((u) => (u.id === account.id ? { ...u, routes: ctx.before } : u)));
      toast.error(`Could not update ${account.name}'s pages: ${e.message}`);
    },
    onSuccess: (u) => toast.success(`${u.name} can now use: ${u.routes.map((r) => ROUTE_LABEL[r]).join(", ")}`),
    onSettled: () => qc.invalidateQueries({ queryKey: keys.users }),
  });
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1">
      {ROUTES.map((r) => {
        const on = account.routes.includes(r);
        const last = on && account.routes.length === 1;
        return (
          <label key={r} className="flex items-center gap-2 text-sm" title={last ? "Each member needs at least one page" : undefined}>
            <Checkbox checked={on} disabled={last || save.isPending}
              aria-label={`${ROUTE_LABEL[r]} page for ${account.name}`}
              aria-describedby={last ? `last-${account.id}` : undefined}
              onCheckedChange={(next) => save.mutate(toggled(account.routes, r, next))} />
            {ROUTE_LABEL[r]}
          </label>
        );
      })}
      {account.routes.length === 1 && <span id={`last-${account.id}`} className="sr-only">Each member needs at least one page.</span>}
    </div>
  );
}
