export default function ProtectedDashboardPage() {
  return (
    <div className="rounded-xl border bg-card p-8 text-card-foreground shadow-sm">
      <h2 className="text-lg font-semibold">Protected area</h2>
      <p className="mt-2 text-sm text-muted-foreground">
        This page is rendered inside the (protected) layout, under the pill navbar.
      </p>
    </div>
  );
}
