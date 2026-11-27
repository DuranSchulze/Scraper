import { ChangePasswordForm } from "@/components/auth/change-password-form";

export const metadata = { title: "Change password · Workspace Scraper" };

// Outside the (app) shell: an account on a temporary password may call nothing the shell polls.
export default function Page() {
  return (
    <main className="flex min-h-screen items-center justify-center p-6 sm:p-10">
      <ChangePasswordForm />
    </main>
  );
}
