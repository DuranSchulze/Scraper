"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Pill } from "@/components/review/badges";
import { Pick } from "@/components/review/pick";
import { keys, meQuery, post, usersQuery } from "@/lib/api";
import { stamp } from "@/lib/format";
import type { Account, Role } from "@/lib/types";

const ROLES = [{ value: "member", label: "Member" }, { value: "admin", label: "Admin" }];

export function AccountsView() {
  const { data: me, isPending: meLoading } = useQuery(meQuery);
  const isAdmin = me?.role === "admin";
  const { data: users, error, isPending } = useQuery({ ...usersQuery, enabled: isAdmin });
  const [open, setOpen] = useState(false);

  if (!meLoading && !isAdmin) {
    return (
      <div className="flex flex-col gap-2">
        <h1 className="text-xl font-semibold tracking-tight">Accounts</h1>
        <p role="alert" className="py-8 text-center text-sm text-muted-foreground">Only administrators can manage accounts.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold tracking-tight">Accounts</h1>
        <Button size="sm" onClick={() => setOpen(true)} disabled={!isAdmin}><Plus className="h-4 w-4 shrink-0" /> New account</Button>
      </div>
      <Card className="gap-0 overflow-hidden p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Email</TableHead>
              <TableHead>Role</TableHead>
              <TableHead>Created</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(users ?? []).map((u: Account) => (
              <TableRow key={u.id} className="hover:bg-muted/40">
                <TableCell className="font-medium">{u.name}</TableCell>
                <TableCell className="break-all">{u.email}</TableCell>
                <TableCell><Pill tone={u.role === "admin" ? "info" : "neutral"}>{u.role === "admin" ? "Admin" : "Member"}</Pill></TableCell>
                <TableCell className="tabular-nums text-muted-foreground">{stamp(u.created_at)}</TableCell>
              </TableRow>
            ))}
            {error && <TableRow><TableCell colSpan={4} role="alert" className="py-8 text-center text-destructive">{error.message}</TableCell></TableRow>}
            {!error && !isPending && !users?.length && <TableRow><TableCell colSpan={4} className="py-8 text-center text-muted-foreground">No accounts yet.</TableCell></TableRow>}
            {!error && isPending && isAdmin && <TableRow><TableCell colSpan={4} className="py-8 text-center text-muted-foreground">Loading accounts…</TableCell></TableRow>}
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
  const [error, setError] = useState("");
  const create = useMutation({
    mutationFn: () => post<Account>("/api/users", { name, email, password, role }),
    onSuccess: (u) => { toast.success(`Account created for ${u.email}`); qc.invalidateQueries({ queryKey: keys.users }); onClose(); },
    onError: (e: Error) => setError(e.message),
  });
  const valid = name.trim() && email.trim() && password.length >= 10;

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
            <Input id="acc-password" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} aria-describedby="acc-password-hint" />
            <p id="acc-password-hint" className="text-xs text-muted-foreground">At least 10 characters.</p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="acc-role">Role</Label>
            <Pick id="acc-role" label="Role" value={role} options={ROLES} onChange={(v) => setRole(v as Role)} />
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
