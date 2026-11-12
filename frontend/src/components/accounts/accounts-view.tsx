"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, Pencil, Plus, Trash2 } from "lucide-react";
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
import { ConfirmDialog } from "@/components/review/confirm-dialog";
import { PageHeader } from "@/components/shell/page-header";
import { Pick } from "@/components/review/pick";
import { deleteRole, keys, post, rolesQuery, saveRole, setUserRole, usersQuery } from "@/lib/api";
import { stamp } from "@/lib/format";
import { ROUTES, type Account, type Role, type RouteKey } from "@/lib/types";

const ROUTE_LABEL: Record<RouteKey, string> = {
  review: "Review", scraping: "Scraping", masterlist: "Master list import", accounts: "Accounts",
};
const ROUTE_HINT: Record<RouteKey, string> = {
  review: "Approve or reject scraped records.",
  scraping: "Run and watch mailbox scrapes.",
  masterlist: "Upload the accounting master list from the client directory. Opens no page here.",
  accounts: "Manage accounts and roles.",
};
// Keeps the API's canonical order whatever order the boxes are ticked in.
const toggled = (routes: RouteKey[], r: RouteKey, on: boolean) => ROUTES.filter((k) => (k === r ? on : routes.includes(k)));
const pages = (routes: RouteKey[]) => routes.map((r) => ROUTE_LABEL[r]).join(", ");

export function AccountsView() {
  const { data: users, error, isPending } = useQuery(usersQuery);
  const { data: roles } = useQuery(rolesQuery);
  const [open, setOpen] = useState(false);
  const roleOptions = (roles ?? []).map((r) => ({ value: String(r.id), label: r.name }));

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Accounts" description="People who can sign in, and the role that decides which pages each one may use."
        actions={<Button size="sm" onClick={() => setOpen(true)} disabled={!roles}><Plus className="h-4 w-4 shrink-0" /> New account</Button>} />
      <Card className="gap-0 overflow-hidden p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Email</TableHead>
              <TableHead className="w-56">Role</TableHead>
              <TableHead>Created</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(users ?? []).map((u: Account) => (
              <TableRow key={u.id} className="hover:bg-muted/40">
                <TableCell className="font-medium">{u.name}</TableCell>
                <TableCell className="break-all">{u.email}</TableCell>
                <TableCell><RoleSelect account={u} options={roleOptions} /></TableCell>
                <TableCell className="tabular-nums text-muted-foreground">{stamp(u.created_at)}</TableCell>
              </TableRow>
            ))}
            {error && <TableRow><TableCell colSpan={4} role="alert" className="py-8 text-center text-destructive">{error.message}</TableCell></TableRow>}
            {!error && !isPending && !users?.length && <TableRow><TableCell colSpan={4} className="py-8 text-center text-muted-foreground">No accounts yet.</TableCell></TableRow>}
            {!error && isPending && Array.from({ length: 3 }, (_, i) => (
              <TableRow key={`sk-${i}`} className="hover:bg-transparent">
                <TableCell>{i === 0 && <LoadingLabel>Loading accounts…</LoadingLabel>}<Skeleton className="h-4 w-28" /></TableCell>
                <TableCell><Skeleton className="h-4 w-44" /></TableCell>
                <TableCell><Skeleton className="h-8 w-40" /></TableCell>
                <TableCell><Skeleton className="h-4 w-28" /></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
      <RolesSection roles={roles} />
      {/* Remounted per open so the draft always starts empty. */}
      {open && roles && <NewAccountDialog roles={roles} onClose={() => setOpen(false)} />}
    </div>
  );
}

/** Saves on change; a refused change (lockout guards) snaps back with the API's reason. */
function RoleSelect({ account, options }: { account: Account; options: { value: string; label: string }[] }) {
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: (roleId: number) => setUserRole(account.id, roleId),
    onSuccess: (u) => {
      toast.success(`${u.name} is now ${u.role.name}`);
      qc.invalidateQueries({ queryKey: keys.roles });
      // Changing your own role changes what you may open.
      qc.invalidateQueries({ queryKey: keys.me });
    },
    onError: (e: Error) => toast.error(`Could not change ${account.name}'s role: ${e.message}`),
    onSettled: () => qc.invalidateQueries({ queryKey: keys.users }),
  });
  // Locked while saving, so a second pick can't race the first and leave the earlier role saved.
  const value = String(save.isPending ? save.variables : account.role.id);
  if (!options.length) return <span className="text-sm">{account.role.name}</span>;
  return (
    <Pick label={`Role for ${account.name}`} value={value} options={options}
      onChange={(v) => { if (v && Number(v) !== account.role.id) save.mutate(Number(v)); }} className="h-8 max-w-52" disabled={save.isPending} />
  );
}

function RolesSection({ roles }: { roles: Role[] | undefined }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<Role | "new" | null>(null);
  const [removing, setRemoving] = useState<Role | null>(null);
  const remove = useMutation({
    mutationFn: (r: Role) => deleteRole(r.id),
    onSuccess: (_d, r) => { toast.success(`Role ${r.name} deleted`); qc.invalidateQueries({ queryKey: keys.roles }); },
    onError: (e: Error) => toast.error(`Could not delete the role: ${e.message}`),
    onSettled: () => setRemoving(null),
  });

  return (
    <section aria-labelledby="roles-heading" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 id="roles-heading" className="text-base font-semibold tracking-tight">Roles</h2>
          <p className="text-sm text-muted-foreground">Each role grants a set of pages. Owner always has every page and cannot change.</p>
        </div>
        <Button size="sm" variant="outline" onClick={() => setEditing("new")}><Plus className="h-4 w-4 shrink-0" /> New role</Button>
      </div>
      <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {!roles && Array.from({ length: 3 }, (_, i) => (
          <li key={i} className="flex flex-col gap-2 rounded-xl bg-muted/40 p-4">
            {i === 0 && <LoadingLabel>Loading roles…</LoadingLabel>}
            <Skeleton className="h-5 w-32" /><Skeleton className="h-4 w-48" />
          </li>
        ))}
        {roles?.map((r) => (
          <li key={r.id} className="flex flex-col gap-3 rounded-xl bg-muted/40 p-4 transition-transform duration-150 hover:-translate-y-0.5 motion-reduce:transition-none motion-reduce:hover:translate-y-0">
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <h3 className="flex items-center gap-1.5 font-medium">
                  <span className="truncate">{r.name}</span>
                  {r.locked && <Lock aria-label="Locked role" className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
                </h3>
                <p className="text-xs text-muted-foreground tabular-nums">{r.users === 1 ? "1 account" : `${r.users.toLocaleString()} accounts`}</p>
              </div>
              {!r.locked && (
                <div className="flex shrink-0 gap-1">
                  <Button size="icon" variant="ghost" className="size-8" aria-label={`Edit role ${r.name}`} onClick={() => setEditing(r)}>
                    <Pencil className="h-4 w-4 shrink-0" />
                  </Button>
                  <Button size="icon" variant="ghost" className="size-8 hover:bg-destructive/10 hover:text-destructive" aria-label={`Delete role ${r.name}`}
                    disabled={r.users > 0} title={r.users > 0 ? "Move its accounts to another role first" : undefined} onClick={() => setRemoving(r)}>
                    <Trash2 className="h-4 w-4 shrink-0" />
                  </Button>
                </div>
              )}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {r.routes.map((k) => <Pill key={k} tone={k === "accounts" ? "info" : "neutral"}>{ROUTE_LABEL[k]}</Pill>)}
            </div>
          </li>
        ))}
      </ul>
      {editing && <RoleDialog role={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
      <ConfirmDialog open={!!removing} onOpenChange={(o) => !o && !remove.isPending && setRemoving(null)} pending={remove.isPending}
        title={`Delete the ${removing?.name ?? ""} role?`} description="No account uses it. This cannot be undone."
        confirmLabel="Delete role" onConfirm={() => removing && remove.mutate(removing)} />
    </section>
  );
}

function RoleDialog({ role, onClose }: { role: Role | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(role?.name ?? "");
  const [routes, setRoutes] = useState<RouteKey[]>(role?.routes ?? []);
  const [error, setError] = useState("");
  const save = useMutation({
    mutationFn: () => saveRole({ name: name.trim(), routes }, role?.id),
    onSuccess: (r) => {
      toast.success(role ? `Role ${r.name} saved: ${pages(r.routes)}` : `Role ${r.name} created`);
      qc.invalidateQueries({ queryKey: keys.roles });
      qc.invalidateQueries({ queryKey: keys.users });
      qc.invalidateQueries({ queryKey: keys.me });
      onClose();
    },
    onError: (e: Error) => setError(e.message),
  });
  const valid = name.trim() && routes.length > 0;

  return (
    <Dialog open onOpenChange={(o) => { if (!o && !save.isPending) onClose(); }}>
      <DialogContent>
        <form onSubmit={(e) => { e.preventDefault(); setError(""); save.mutate(); }} className="flex flex-col gap-4" noValidate>
          <DialogHeader>
            <DialogTitle>{role ? `Edit ${role.name}` : "New role"}</DialogTitle>
            <DialogDescription>Accounts with this role can open the pages you tick. Changes apply on their next click.</DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="role-name">Name</Label>
            <Input id="role-name" autoComplete="off" maxLength={100} value={name} onChange={(e) => setName(e.target.value)} placeholder="Accounting" />
          </div>
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-sm font-medium">Pages</legend>
            {ROUTES.map((r) => (
              <label key={r} className="flex cursor-pointer items-start gap-3 rounded-lg px-2 py-1.5 hover:bg-muted/50">
                <Checkbox className="mt-0.5" checked={routes.includes(r)} onCheckedChange={(on) => setRoutes((cur) => toggled(cur, r, on))} />
                <span className="flex flex-col">
                  <span className="text-sm font-medium">{ROUTE_LABEL[r]}</span>
                  <span className="text-xs text-muted-foreground">{ROUTE_HINT[r]}</span>
                </span>
              </label>
            ))}
            {!routes.length && <p className="text-xs text-destructive">Pick at least one page.</p>}
          </fieldset>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={save.isPending}>Cancel</Button>
            <Button type="submit" disabled={!valid || save.isPending}>{save.isPending ? "Saving…" : role ? "Save role" : "Create role"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function NewAccountDialog({ roles, onClose }: { roles: Role[]; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // Default to the first unlocked role, so a new account is never an Owner by accident.
  const [roleId, setRoleId] = useState(String((roles.find((r) => !r.locked) ?? roles[0])?.id ?? ""));
  const [error, setError] = useState("");
  const role = roles.find((r) => String(r.id) === roleId);
  const create = useMutation({
    mutationFn: () => post<Account>("/api/users", { name, email, password, role_id: Number(roleId) }),
    onSuccess: (u) => {
      toast.success(`Account created for ${u.email}`);
      qc.invalidateQueries({ queryKey: keys.users });
      qc.invalidateQueries({ queryKey: keys.roles });
      onClose();
    },
    onError: (e: Error) => setError(e.message),
  });
  const valid = name.trim() && email.trim() && password && roleId;

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
            <Pick id="acc-role" label="Role" value={roleId} options={roles.map((r) => ({ value: String(r.id), label: r.name }))} onChange={setRoleId} />
            {role && <p className="text-xs text-muted-foreground">Can use: {pages(role.routes)}{role.locked ? ". Owners can change every account and role." : ""}</p>}
          </div>
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
