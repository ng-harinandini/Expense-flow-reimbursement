"use client";

import * as React from "react";
import type { ColDef } from "ag-grid-community";
import { BadgeCheck, Search } from "lucide-react";

import { DataGrid } from "@/components/shared/DataGrid";
import { Input } from "@/components/ui/input";
import { useClaimsQuery, useExecuteClaimActionMutation } from "@/api/claims";
import { getErrorMessage } from "@/lib/apiError";
import { useToast } from "@/components/ui/toast";
import type { Claim, ClaimStatus } from "@/types";

import { useExpandableItems } from "@/components/my_claims/useExpandableItems";
import { CENTERED_COL_DEF } from "@/components/my_claims/columns";

import { buildColumnDefs, isFinanceRejected, managerNameFor } from "./columns";
import { FinanceReviewDialog } from "./FinanceReviewDialog";

const RELEVANT_STATUSES: ClaimStatus[] = ["Finance_Review", "Approved", "Disbursed", "Rejected"];

// Tall enough for the stacked claim-title + date-range cell.
const ROW_HEIGHT = 64;

function FinanceApprovals() {
  const toast = useToast();
  const [search, setSearch] = React.useState("");
  const [selectedClaim, setSelectedClaim] = React.useState<Claim | null>(null);
  const [isDetailOpen, setIsDetailOpen] = React.useState(false);

const { data: fetchedClaims = [], isLoading, error } = useClaimsQuery({
    status: RELEVANT_STATUSES,
  });
  const allClaims = React.useMemo(
    () => fetchedClaims.filter((claim) => claim.status !== "Rejected" || isFinanceRejected(claim)),
    [fetchedClaims]
  );

  const actionMutation = useExecuteClaimActionMutation();

  const handleView = React.useCallback((claim: Claim) => {
    setSelectedClaim(claim);
    setIsDetailOpen(true);
  }, []);

  // Approve is finance's final reviewer step — there is no separate disbursement action to chain
  // to any more (see app.domain.claim_state_machine on the backend).
  const handleApprove = React.useCallback(
    async (claim: Claim) => {
      try {
        await actionMutation.mutateAsync({ claimId: claim.id, action: "APPROVE" });
        toast({ message: `${claim.claimNumber} approved.`, type: "success" });
      } catch (err) {
        toast({
          message: getErrorMessage(err, "Failed to approve this claim."),
          type: "error",
        });
      }
    },
    [actionMutation, toast]
  );

  const handleReject = React.useCallback(
    async (claim: Claim, reason: string) => {
      try {
        await actionMutation.mutateAsync({
          claimId: claim.id,
          action: "REJECT",
          notes: reason,
        });
        toast({ message: `${claim.claimNumber} rejected.`, type: "success" });
      } catch (err) {
        toast({
          message: getErrorMessage(err, "Failed to reject this claim."),
          type: "error",
        });
      }
    },
    [actionMutation, toast]
  );

  const columnDefs = React.useMemo<ColDef<Claim>[]>(
    () => buildColumnDefs(handleView),
    [handleView]
  );

  const rowData = React.useMemo(() => {
    const query = search.trim().toLowerCase();

    return allClaims.filter((claim) => {
      return (
        !query ||
        claim.claimNumber.toLowerCase().includes(query) ||
        claim.claimTitle.toLowerCase().includes(query) ||
        claim.employeeName.toLowerCase().includes(query) ||
        managerNameFor(claim).toLowerCase().includes(query)
      );
    });
  }, [allClaims, search]);

  const defaultColDef = React.useMemo<ColDef>(
    () => ({
      resizable: true,
      sortable: true,
      filter: false,
      ...CENTERED_COL_DEF,
    }),
    []
  );

  const expandableProps = useExpandableItems(rowData, columnDefs, ROW_HEIGHT);

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
              Review manager-approved claims pending finance sign-off, plus what finance has
              already approved or rejected
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
        {error ? (
          <div className="flex h-full items-center justify-center text-sm text-destructive">
            {getErrorMessage(error, "Failed to load claims.")}
          </div>
        ) : (
          <DataGrid
            {...expandableProps}
            defaultColDef={defaultColDef}
            loading={isLoading}
            overlayNoRowsTemplate="No claims found matching your filter criteria."
            domLayout="normal"
            rowHeight={ROW_HEIGHT}
            headerHeight={44}
            suppressCellFocus
          />
        )}
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
