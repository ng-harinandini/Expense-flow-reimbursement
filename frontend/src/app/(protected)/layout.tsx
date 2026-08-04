import { AppShell } from "@/components/shared/AppShell";
import { RequireAuth } from "@/components/authentication/RequireAuth";

export default function ProtectedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <RequireAuth>
      <AppShell>{children}</AppShell>
    </RequireAuth>
  );
}
