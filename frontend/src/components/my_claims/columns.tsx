import type { ColDef, ICellRendererParams } from "ag-grid-community";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { Claim, ClaimStatus, ExpenseItemStatus } from "@/types";

import {
  ITEM_STATUS_BADGE_CLASSES,
  ITEM_STATUS_LABELS,
  STATUS_BADGE_CLASSES,
  STATUS_LABELS,
} from "./status";

export function StatusBadge({ status }: { status: ClaimStatus }) {
  return (
    <Badge className={cn("font-medium", STATUS_BADGE_CLASSES[status])}>
      {STATUS_LABELS[status]}
    </Badge>
  );
}

export function ItemStatusBadge({
  status,
  className,
}: {
  status: ExpenseItemStatus;
  className?: string;
}) {
  return (
    <Badge
      className={cn(
        "font-medium",
        ITEM_STATUS_BADGE_CLASSES[status],
        className,
      )}
    >
      {ITEM_STATUS_LABELS[status]}
    </Badge>
  );
}

export function formatCurrency(amount: number, currency: string = "INR") {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: currency || "INR",
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

/** Date *and* time, for audit-style stamps like the withdrawal record. */
export function formatDateTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("en-US", {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}


/**
 * Compact range for the line under a claim title: "Jul 20 – Jul 24, 2026". The year is printed
 * once when both ends share it, and on both ends when the range crosses a year boundary.
 */
export function formatDateRange(fromDate: string, toDate: string) {
  const from = new Date(fromDate);
  const to = new Date(toDate);
  if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime())) {
    return `${formatDate(fromDate)} – ${formatDate(toDate)}`;
  }

  const monthDay: Intl.DateTimeFormatOptions = { month: "short", day: "2-digit" };
  const sameYear = from.getFullYear() === to.getFullYear();

  const fromLabel = from.toLocaleDateString(
    "en-US",
    sameYear ? monthDay : { ...monthDay, year: "numeric" },
  );
  const toLabel = to.toLocaleDateString("en-US", { ...monthDay, year: "numeric" });

  return `${fromLabel} – ${toLabel}`;
}

/** Claim title with the date range stacked underneath, as one grid cell. */
export function ClaimTitleCell({ claim }: { claim: Claim }) {
  return (
    // `min-w-0` lets the title truncate instead of forcing the cell wider than its track.
    <div className="flex min-w-0 flex-col items-center justify-center gap-0.5 text-center leading-tight">
      <span
        className="w-full truncate font-medium text-foreground"
        title={claim.claimTitle}
      >
        {claim.claimTitle}
      </span>
      <span className="w-full truncate text-xs text-muted-foreground">
        {formatDateRange(claim.fromDate, claim.toDate)}
      </span>
    </div>
  );
}

/** Item count only — the chips themselves live in the expandable detail row. */
export function ItemCountCell({ claim }: { claim: Claim }) {
  const count = claim.items.length;
  return (
    <span className="text-sm text-muted-foreground">
      {count} {count === 1 ? "item" : "items"}
    </span>
  );
}

/**
 * Centres every column, header included. Spread into each grid's `defaultColDef`.
 *
 * AG Grid lays cells out with its own padding and most columns here use custom renderers, so
 * `text-align` isn't enough — the cell is made a centring flex container, and the header label
 * gets a matching rule so it doesn't stay left-aligned above a centred value.
 */
export const CENTERED_COL_DEF = {
  headerClass: "[&_.ag-header-cell-label]:justify-center",
  cellStyle: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  },
} as const;

export function buildColumnDefs(onView: (claim: Claim) => void): ColDef<Claim>[] {
  return [
    {
      headerName: "Claim Ref",
      field: "claimNumber",
      flex: 1,
      minWidth: 140,
      cellClass: "font-medium",
    },
    {
      headerName: "Employee",
      field: "employeeName",
      flex: 1,
      minWidth: 130,
    },
    {
      headerName: "Claim Title",
      // Widest track: it carries two stacked lines, and the date range must not wrap.
      field: "claimTitle",
      flex: 2.2,
      minWidth: 240,
      // Sorts on the title; the date underneath is presentation only.
      cellRenderer: (params: ICellRendererParams<Claim>) =>
        params.data ? <ClaimTitleCell claim={params.data} /> : null,
    },
    {
      headerName: "Items",
      colId: "itemCount",
      flex: 0.7,
      minWidth: 90,
      valueGetter: (params) => (params.data ? params.data.items.length : 0),
      cellRenderer: (params: ICellRendererParams<Claim>) =>
        params.data ? <ItemCountCell claim={params.data} /> : null,
    },
    {
      headerName: "Total Amount",
      field: "totalAmount",
      flex: 1,
      minWidth: 130,
      cellClass: "font-semibold",
      // valueFormatter: (params) =>
      //   formatCurrency(params.value as number, params.data?.currency),
    },
    {
      headerName: "Claim Status",
      field: "status",
      flex: 1.1,
      minWidth: 150,
      cellRenderer: (params: ICellRendererParams<Claim>) =>
        params.data ? <StatusBadge status={params.data.status} /> : null,
    },
    {
      headerName: "Action",
      colId: "action",
      flex: 0.7,
      minWidth: 100,
      sortable: false,
      filter: false,
      cellRenderer: (params: ICellRendererParams<Claim>) => (
        <button
          type="button"
          className="text-sm font-medium text-secondary hover:underline"
          onClick={() => params.data && onView(params.data)}
        >
          View
        </button>
      ),
    },
  ];
}
