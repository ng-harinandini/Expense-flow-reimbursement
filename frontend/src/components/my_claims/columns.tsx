import type * as React from "react";
import type { ColDef, ICellRendererParams } from "ag-grid-community";

import { Badge } from "@/components/ui/badge";
import {
  CLAIM_STATUS_BADGE_CLASSES,
  CLAIM_STATUS_LABELS,
  EXPENSE_ITEM_STATUS_BADGE_CLASSES,
  EXPENSE_ITEM_STATUS_DOT_CLASSES,
  EXPENSE_ITEM_STATUS_LABELS,
} from "@/lib/claimStatus";
import { cn } from "@/lib/utils";
import type { Claim, ClaimStatus, ExpenseItemStatus } from "@/types";

export { CLAIM_STATUS_LABELS as STATUS_LABELS };

export function StatusBadge({ status }: { status: ClaimStatus }) {
  return (
    <Badge className={cn("font-medium", CLAIM_STATUS_BADGE_CLASSES[status])}>
      {CLAIM_STATUS_LABELS[status]}
    </Badge>
  );
}

export function ItemStatusBadge({ status }: { status: ExpenseItemStatus }) {
  return (
    <Badge className={cn("font-medium", EXPENSE_ITEM_STATUS_BADGE_CLASSES[status])}>
      {EXPENSE_ITEM_STATUS_LABELS[status]}
    </Badge>
  );
}

export function ItemStatusDot({ status }: { status: ExpenseItemStatus }) {
  return (
    <span
      className={cn("size-2 shrink-0 rounded-full", EXPENSE_ITEM_STATUS_DOT_CLASSES[status])}
      aria-hidden
    />
  );
}

export function formatCurrency(amount: number, currency: string = "USD") {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: currency || "USD",
  }).format(amount);
}

export function formatDate(dateStr: string) {
  const date = new Date(dateStr);
  if (Number.isNaN(date.getTime())) return dateStr;
  return date.toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "2-digit",
  });
}

/** "Jul 20 – Jul 24, 2026" — year printed once when both dates share it. */
export function formatDateRange(fromDate: string, toDate: string) {
  const from = new Date(fromDate);
  const to = new Date(toDate);
  if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime())) {
    return `${fromDate} – ${toDate}`;
  }

  const shortOpts: Intl.DateTimeFormatOptions = { month: "short", day: "numeric" };
  const longOpts: Intl.DateTimeFormatOptions = { ...shortOpts, year: "numeric" };
  const fromOpts = from.getFullYear() === to.getFullYear() ? shortOpts : longOpts;

  return `${from.toLocaleDateString("en-US", fromOpts)} – ${to.toLocaleDateString("en-US", longOpts)}`;
}

export function getClaimTotal(claim: Claim) {
  return claim.items.reduce((sum, item) => sum + item.amount, 0);
}

/**
 * Every cell aligns its first line to the same baseline row as the Claim Title's
 * bold line. Claim Title is the tallest cell (title + date range), so instead of
 * centering each cell independently — which floats one-line cells below the
 * title — cells are pinned to a shared top offset and the title's second line
 * hangs beneath it.
 */
const CELL_LINE_OFFSET = "pt-[18px]";
/**
 * Badges/buttons are taller than a bare text line, so they start higher to keep
 * their vertical center on the same line as the title text.
 */
const CONTROL_LINE_OFFSET = "pt-[14px]";

/** Wraps single-line cell content so it lands on the shared first-line baseline. */
function CellLine({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <div className={cn("flex h-full flex-col", CELL_LINE_OFFSET)}>
      <span className={cn("leading-tight", className)}>{children}</span>
    </div>
  );
}

/**
 * Segment bars — one per item, colored by that item's status. The bar strip
 * gets a fixed-width slot so the "N items" counts stay aligned down the column
 * regardless of how many items each claim has.
 */
function ItemsCell({ claim }: { claim: Claim }) {
  return (
    <div className={cn("flex h-full flex-col", CELL_LINE_OFFSET)}>
      <div className="flex items-center gap-2 leading-tight">
        {/* <div className="flex w-[92px] shrink-0 items-center gap-1">
          {claim.items.slice(0, 5).map((item) => (
            <span
              key={item.id}
              title={`${item.merchantVendor} — ${EXPENSE_ITEM_STATUS_LABELS[item.status]}`}
              className={cn(
                "h-1.5 w-4 rounded-full",
                EXPENSE_ITEM_STATUS_DOT_CLASSES[item.status]
              )}
            />
          ))}
        </div> */}
        <span className="whitespace-nowrap text-xs text-muted-foreground font-medium">
          {claim.items.length} {claim.items.length === 1 ? "item" : "items"}
        </span>
      </div>
    </div>
  );
}

export function buildColumnDefs(onView: (claim: Claim) => void): ColDef<Claim>[] {
  return [
    {
      headerName: "Claim Ref",
      field: "claimNumber",
      flex: 1.1,
      minWidth: 140,
      cellRenderer: (params: ICellRendererParams<Claim>) =>
        params.data ? (
          <CellLine className="font-medium text-foreground">
            {params.data.claimNumber}
          </CellLine>
        ) : null,
    },
    {
      headerName: "Employee",
      field: "employeeName",
      flex: 1,
      minWidth: 130,
      cellRenderer: (params: ICellRendererParams<Claim>) =>
        params.data ? <CellLine>{params.data.employeeName}</CellLine> : null,
    },
    {
      headerName: "Claim Title",
      field: "claimTitle",
      flex: 1.5,
      minWidth: 190,
      cellRenderer: (params: ICellRendererParams<Claim>) =>
        params.data ? (
          <div className={cn("flex h-full flex-col leading-tight", CELL_LINE_OFFSET)}>
            <span className="font-medium text-foreground">{params.data.claimTitle}</span>
            <span className="text-xs text-muted-foreground">
              {formatDateRange(params.data.fromDate, params.data.toDate)}
            </span>
          </div>
        ) : null,
    },
    {
      headerName: "Items",
      colId: "items",
      flex: 1.1,
      minWidth: 150,
      sortable: false,
      cellClass: "pl-1",
      valueGetter: (params) => params.data?.items.length ?? 0,
      cellRenderer: (params: ICellRendererParams<Claim>) =>
        params.data ? <ItemsCell claim={params.data} /> : null,
    },
    {
      headerName: "Total Amount",
      colId: "totalAmount",
      flex: 1,
      minWidth: 120,
      valueGetter: (params) => (params.data ? getClaimTotal(params.data) : 0),
      cellRenderer: (params: ICellRendererParams<Claim>) =>
        params.data ? (
          <CellLine className="text-foreground">
            {formatCurrency(getClaimTotal(params.data))}
          </CellLine>
        ) : null,
    },
    {
      headerName: "Claim Status",
      field: "status",
      flex: 1.1,
      minWidth: 150,
      cellRenderer: (params: ICellRendererParams<Claim>) =>
        params.data ? (
          <div className={cn("flex h-full flex-col items-start", CONTROL_LINE_OFFSET)}>
            <StatusBadge status={params.data.status} />
          </div>
        ) : null,
    },
    {
      headerName: "Action",
      colId: "action",
      flex: 0.7,
      minWidth: 100,
      sortable: false,
      filter: false,
      cellRenderer: (params: ICellRendererParams<Claim>) => (
        <div className={cn("flex h-full flex-col items-start", CELL_LINE_OFFSET)}>
          <button
            type="button"
            className="text-sm font-medium leading-tight text-secondary hover:underline"
            onClick={() => params.data && onView(params.data)}
          >
            View
          </button>
        </div>
      ),
    },
  ];
}
