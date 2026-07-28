"use client";

import * as React from "react";
import { AgGridReact } from "ag-grid-react";
import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
  type ColDef,
} from "ag-grid-community";
import { BadgeCheck, Search } from "lucide-react";

import { Input } from "@/components/ui/input";
import { INITIAL_MULTI_ITEM_CLAIMS } from "@/data/claims";
import { INITIAL_EMPLOYEES } from "@/data/initialClaims";
import type { Claim, ClaimStatus, WorkflowStepLog } from "@/types";

import { buildColumnDefs, managerNameFor } from "./columns";
import { FinanceReviewDialog } from "./FinanceReviewDialog";

ModuleRegistry.registerModules([AllCommunityModule]);

const PENDING_STATUS: ClaimStatus = "Finance_Review";

function FinanceApprovals() {
  const [claims, setClaims] = React.useState<Claim[]>(INITIAL_MULTI_ITEM_CLAIMS);
  const [search, setSearch] = React.useState("");
  const [selectedClaim, setSelectedClaim] = React.useState<Claim | null>(null);
  const [isDetailOpen, setIsDetailOpen] = React.useState(false);

  const handleView = React.useCallback((claim: Claim) => {
    setSelectedClaim(claim);
    setIsDetailOpen(true);
  }, []);

  // TODO: replace with PATCH /api/claims/:id/status once the endpoint exists.
  const applyDecision = React.useCallback(
    (claim: Claim, nextStatus: ClaimStatus, action: string, notes?: string) => {
      const financeActor = INITIAL_EMPLOYEES.find((e) => e.role === "finance")?.name ?? "Finance";
      const entry: WorkflowStepLog = {
        timestamp: new Date().toISOString(),
        actorName: financeActor,
        actorRole: "finance",
        stepName: "Finance Review",
        action,
        status: nextStatus === "Rejected" ? "FAILED" : "SUCCESS",
        notes,
      };

      setClaims((current) =>
        current.map((c) =>
          c.id === claim.id
            ? { ...c, status: nextStatus, workflowHistory: [...c.workflowHistory, entry] }
            : c
        )
      );
    },
    []
  );

  const handleApprove = React.useCallback(
    (claim: Claim) => applyDecision(claim, "Approved", "Approved claim for disbursement"),
    [applyDecision]
  );

  const handleReject = React.useCallback(
    (claim: Claim, reason: string) => applyDecision(claim, "Rejected", "Rejected claim", reason),
    [applyDecision]
  );

  const columnDefs = React.useMemo<ColDef<Claim>[]>(
    () => buildColumnDefs(handleView),
    [handleView]
  );

  const rowData = React.useMemo(() => {
    const query = search.trim().toLowerCase();

    return claims.filter((claim) => {
      if (claim.status !== PENDING_STATUS) return false;

      return (
        !query ||
        claim.claimNumber.toLowerCase().includes(query) ||
        claim.claimTitle.toLowerCase().includes(query) ||
        claim.employeeName.toLowerCase().includes(query) ||
        managerNameFor(claim).toLowerCase().includes(query)
      );
    });
  }, [claims, search]);

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
            <BadgeCheck className="size-6 text-primary" />
          </div>
          <div className="min-w-0">
            <h2 className="text-xl font-semibold text-foreground">Finance Approvals</h2>
            <p className="text-sm text-muted-foreground">
              Review manager-approved claims pending finance sign-off
            </p>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-3 px-6 pb-4">
        <div className="relative w-1/2">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by claim #, title, employee, manager..."
            className="pl-9 text-foreground"
          />
        </div>
      </div>

      <div className="h-[560px] px-6 pb-6">
        <AgGridReact<Claim>
          theme={themeQuartz}
          rowData={rowData}
          columnDefs={columnDefs}
          defaultColDef={defaultColDef}
          overlayNoRowsTemplate="No claims pending finance approval."
          domLayout="normal"
          rowHeight={56}
          headerHeight={44}
          suppressCellFocus
        />
      </div>

      <FinanceReviewDialog
        claim={selectedClaim}
        open={isDetailOpen}
        onOpenChange={setIsDetailOpen}
        onApprove={handleApprove}
        onReject={handleReject}
      />
    </div>
  );
}

export default FinanceApprovals;
