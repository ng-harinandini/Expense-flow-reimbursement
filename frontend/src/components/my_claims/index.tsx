"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { AgGridReact } from "ag-grid-react";
import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
  type ColDef,
} from "ag-grid-community";
import { FileText, PlusCircle, Search } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { getClaims } from "@/api/claims";
import { getErrorMessage } from "@/lib/apiError";
import type { Claim, ClaimStatus } from "@/types";

import { buildColumnDefs, STATUS_LABELS } from "./columns";
import { ClaimDetailDialog } from "./ClaimDetailDialog";

ModuleRegistry.registerModules([AllCommunityModule]);

const STATUS_OPTIONS: ClaimStatus[] = [
  "Submitted",
  "Auto_Approved",
  "Manager_Review",
  "Finance_Review",
  "Flagged_Fraud",
  "Approved",
  "Disbursed",
];

const ALL_STATUSES = "all-statuses";

function MyClaims() {
  const router = useRouter();
  const [search, setSearch] = React.useState("");
  const [status, setStatus] = React.useState<string>(ALL_STATUSES);
  const [selectedClaim, setSelectedClaim] = React.useState<Claim | null>(null);
  const [isDetailOpen, setIsDetailOpen] = React.useState(false);
  const [allClaims, setAllClaims] = React.useState<Claim[]>([]);
  const [isLoading, setIsLoading] = React.useState(true);
  const [fetchError, setFetchError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setFetchError(null);
    getClaims()
      .then((data) => { if (!cancelled) setAllClaims(data); })
      .catch((err) => { if (!cancelled) setFetchError(getErrorMessage(err, "Failed to load claims.")); })
      .finally(() => { if (!cancelled) setIsLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const handleView = React.useCallback((claim: Claim) => {
    setSelectedClaim(claim);
    setIsDetailOpen(true);
  }, []);

  const columnDefs = React.useMemo<ColDef<Claim>[]>(
    () => buildColumnDefs(handleView),
    [handleView]
  );

  const rowData = React.useMemo(() => {
    const query = search.trim().toLowerCase();

    return allClaims.filter((claim) => {
      const matchesSearch =
        !query ||
        claim.claimNumber.toLowerCase().includes(query) ||
        claim.claimTitle.toLowerCase().includes(query) ||
        claim.employeeName.toLowerCase().includes(query);

      const matchesStatus = status === ALL_STATUSES || claim.status === status;

      return matchesSearch && matchesStatus;
    });
  }, [search, status, allClaims]);

  const defaultColDef = React.useMemo<ColDef>(
    () => ({
      resizable: true,
      sortable: true,
      filter: false,
    }),
    []
  );

  return (
    <div className="rounded-xl border bg-card shadow-sm">
      <div className="flex flex-col gap-4 p-6 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-4">
          <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-primary/10">
            <FileText className="size-6 text-primary" />
          </div>
          <div className="min-w-0">
            <h2 className="text-xl font-semibold text-foreground">My Expense Claims</h2>
            <p className="text-sm text-muted-foreground">
              Review claims, AI policy violations, and fraud risk scores
            </p>
          </div>
        </div>
        <Button
          className="w-full sm:w-auto"
          onClick={() => router.push("/submit-expense")}
        >
          <PlusCircle />
          Submit New Expense
        </Button>
      </div>

      <div className="flex items-center gap-3 px-6 pb-4">
        <div className="relative w-1/2">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by claim #, title, employee..."
            className="pl-9 text-foreground"
          />
        </div>

        <div className="flex-1" />

        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger className="h-9 text-foreground sm:w-48">
            <SelectValue placeholder="All Statuses" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_STATUSES}>All Statuses</SelectItem>
            {STATUS_OPTIONS.map((option) => (
              <SelectItem key={option} value={option}>
                {STATUS_LABELS[option]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="h-[560px] px-6 pb-6">
        {fetchError ? (
          <div className="flex h-full items-center justify-center text-sm text-destructive">
            {fetchError}
          </div>
        ) : (
          <AgGridReact<Claim>
            theme={themeQuartz}
            rowData={rowData}
            columnDefs={columnDefs}
            defaultColDef={defaultColDef}
            loading={isLoading}
            overlayNoRowsTemplate="No claims found matching your filter criteria."
            domLayout="normal"
            rowHeight={56}
            headerHeight={44}
            suppressCellFocus
          />
        )}
      </div>

      <ClaimDetailDialog
        claim={selectedClaim}
        open={isDetailOpen}
        onOpenChange={setIsDetailOpen}
      />
    </div>
  );
}

export default MyClaims;
