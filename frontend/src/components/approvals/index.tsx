"use client";

import * as React from "react";
import { AgGridReact } from "ag-grid-react";
import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
  type ColDef,
} from "ag-grid-community";
import { ClipboardCheck, Search } from "lucide-react";

import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { INITIAL_MULTI_ITEM_CLAIMS } from "@/data/claims";
import { INITIAL_EMPLOYEES } from "@/data/initialClaims";
import { CLAIM_STATUSES, CLAIM_STATUS_LABELS } from "@/lib/claimStatus";
import type { Claim, ClaimStatus, WorkflowStepLog } from "@/types";

import { buildColumnDefs } from "./columns";
import { ClaimReviewDialog } from "./ClaimReviewDialog";

ModuleRegistry.registerModules([AllCommunityModule]);

const ALL_STATUSES = "all-statuses";

function Approvals() {
  const [claims, setClaims] = React.useState<Claim[]>(INITIAL_MULTI_ITEM_CLAIMS);
  const [search, setSearch] = React.useState("");
  const [status, setStatus] = React.useState<string>(ALL_STATUSES);
  const [selectedClaim, setSelectedClaim] = React.useState<Claim | null>(null);
  const [isDetailOpen, setIsDetailOpen] = React.useState(false);

  const handleView = React.useCallback((claim: Claim) => {
    setSelectedClaim(claim);
    setIsDetailOpen(true);
  }, []);

  // TODO: replace with PATCH /api/claims/:id/status once the endpoint exists.
  const applyDecision = React.useCallback(
    (claim: Claim, nextStatus: ClaimStatus, action: string, notes?: string) => {
      const manager = INITIAL_EMPLOYEES.find((e) => e.id === claim.employeeId)?.managerName;
      const entry: WorkflowStepLog = {
        timestamp: new Date().toISOString(),
        actorName: manager ?? "Manager",
        actorRole: "manager",
        stepName: "Manager Review",
        action,
        status: nextStatus === "rejected" ? "FAILED" : nextStatus === "draft" ? "WARNING" : "SUCCESS",
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
    (claim: Claim) => applyDecision(claim, "finance_review", "Approved claim, sent to finance"),
    [applyDecision]
  );

  const handleReject = React.useCallback(
    (claim: Claim, reason: string) => applyDecision(claim, "rejected", "Rejected claim", reason),
    [applyDecision]
  );

  const handleSendBack = React.useCallback(
    (claim: Claim, reason: string) =>
      applyDecision(claim, "draft", "Sent back to employee for changes", reason),
    [applyDecision]
  );

  const columnDefs = React.useMemo<ColDef<Claim>[]>(
    () => buildColumnDefs(handleView),
    [handleView]
  );

  const rowData = React.useMemo(() => {
    const query = search.trim().toLowerCase();

    return claims.filter((claim) => {
      const matchesSearch =
        !query ||
        claim.claimNumber.toLowerCase().includes(query) ||
        claim.claimTitle.toLowerCase().includes(query) ||
        claim.employeeName.toLowerCase().includes(query);

      const matchesStatus = status === ALL_STATUSES || claim.status === status;

      return matchesSearch && matchesStatus;
    });
  }, [claims, search, status]);

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
            <ClipboardCheck className="size-6 text-primary" />
          </div>
          <div className="min-w-0">
            <h2 className="text-xl font-semibold text-foreground">Team Expense Claims</h2>
            <p className="text-sm text-muted-foreground">
              Review expense claims raised by your team
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
            {CLAIM_STATUSES.map((option) => (
              <SelectItem key={option} value={option}>
                {CLAIM_STATUS_LABELS[option]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="h-[560px] px-6 pb-6">
        <AgGridReact<Claim>
          theme={themeQuartz}
          rowData={rowData}
          columnDefs={columnDefs}
          defaultColDef={defaultColDef}
          overlayNoRowsTemplate="No claims found matching your filter criteria."
          domLayout="normal"
          rowHeight={56}
          headerHeight={44}
          suppressCellFocus
        />
      </div>

      <ClaimReviewDialog
        claim={selectedClaim}
        open={isDetailOpen}
        onOpenChange={setIsDetailOpen}
        onApprove={handleApprove}
        onReject={handleReject}
        onSendBack={handleSendBack}
      />
    </div>
  );
}

export default Approvals;
