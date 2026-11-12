import { AccountsView } from "@/components/accounts/accounts-view";
import { RouteGuard } from "@/components/shell/route-guard";

export default function Page() {
  return (
    <RouteGuard route="accounts">
      <AccountsView />
    </RouteGuard>
  );
}
