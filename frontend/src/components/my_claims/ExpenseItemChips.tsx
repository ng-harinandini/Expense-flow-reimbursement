"use client";

import type { ColDef, ICellRendererParams } from "ag-grid-community";
import { ChevronRight } from "lucide-react";

import { cn } from "@/lib/utils";
import type { Claim, ClaimExpenseItem, ClaimStatus, ExpenseCategory } from "@/types";

import { STATUS_LABELS } from "./status";

/** Dot colour per category, so a chip is scannable before its text is read. */
const CATEGORY_DOT_CLASSES: Record<string, string> = {
  Flights: "bg-amber-500",
  Lodging: "bg-emerald-500",
  Meals: "bg-emerald-500",
  "Ground Transport": "bg-amber-500",
  "Client Entertainment": "bg-emerald-500",
  "Software & Subscriptions": "bg-blue-500",
};

const FALLBACK_DOT_CLASS = "bg-muted-foreground";

function dotClassFor(category: ExpenseCategory) {
  return CATEGORY_DOT_CLASSES[category] ?? FALLBACK_DOT_CLASS;
}

export function ExpenseItemChip({ item }: { item: ClaimExpenseItem }) {
  return (
    <span className="inline-flex items-center gap-2 rounded-full border bg-card px-3 py-1.5 text-xs shadow-sm">
      <span className={cn("size-1.5 shrink-0 rounded-full", dotClassFor(item.category))} />
      <span className="font-medium text-foreground">{item.merchantVendor}</span>
      <span className="text-muted-foreground">
        {item.amount.toFixed(2)} 
      </span>
    </span>
  );
}

function getStatusReason(claim: Claim): string {
  const count = claim.items.length;
  const itemLabel = count === 1 ? "1 item" : `${count} items`;
  const total = claim.totalAmount.toFixed(2);

  const reasons: Record<ClaimStatus, string> = {
    Draft: `This claim hasn't been submitted yet — ${itemLabel} totalling ${total} so far.`,
    Submitted: `${itemLabel} totalling ${total} have been received and are queued for AI scanning.`,
    Processing_AI: `${itemLabel} totalling ${total} are being scanned for receipt data and policy compliance.`,
    Auto_Approved: `${itemLabel} totalling ${total} passed policy checks within auto-approval limits, so no manual review was needed.`,
    Manager_Review: `${itemLabel} totalling ${total} need manager sign-off before this moves to finance.`,
    Finance_Review: `The manager approved ${itemLabel} totalling ${total}; finance is verifying before disbursement.`,
    Approved: `${itemLabel} totalling ${total} cleared review and are approved for disbursement.`,
    Rejected: `${itemLabel} totalling ${total} were reviewed and rejected — see the claim's comments for details.`,
    Disbursed: `Payment for ${itemLabel} totalling ${total} has been issued.`,
    Flagged_Fraud: `${itemLabel} totalling ${total} were flagged during fraud screening and are under investigation.`,
    Withdrawn: `This claim was withdrawn, so ${itemLabel} totalling ${total} are no longer under review.`,
  };

  return reasons[claim.status];
}

/** The "Why 'Manager review'?" explainer line above the chips. */
export function StatusReason({ claim }: { claim: Claim }) {
  return (
    <p className="text-xs text-muted-foreground">
      <span className="font-semibold text-foreground">
        Why &ldquo;{STATUS_LABELS[claim.status]}&rdquo;?
      </span>{" "}
      {getStatusReason(claim)}
    </p>
  );
}

/** The full-width detail row: every item on the claim, as chips. */
export function ExpenseItemsDetailRow({ claim }: { claim: Claim }) {
  // `pl-[52px]` matches the chevron column so content starts under the first data column.
  return (
    <div className="flex h-full flex-col justify-center gap-2 border-b bg-muted/30 py-2 pl-[52px] pr-4">
      <StatusReason claim={claim} />
      {claim.items.length === 0 ? (
        <p className="text-xs text-muted-foreground">No expense items on this claim.</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          {claim.items.map((item) => (
            <ExpenseItemChip key={item.id} item={item} />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * A grid row that is really the expanded panel for `__detailOf`, not a claim. Rows are typed as
 * `Claim` throughout the grids, so the marker rides along as an optional extra field rather than
 * forcing a union type through every column definition.
 */
export type ClaimRow = Claim & { __detailOf?: Claim };

export function isDetailRow(row: ClaimRow | undefined): boolean {
  return Boolean(row?.__detailOf);
}

/** Chevron toggle column. Prepend to a grid's `columnDefs` as the first entry. */
export function buildExpandColumn(
  isExpanded: (claim: Claim) => boolean,
  onToggle: (claim: Claim) => void,
): ColDef<ClaimRow> {
  return {
    colId: "expand",
    headerName: "",
    width: 52,
    minWidth: 52,
    maxWidth: 52,
    sortable: false,
    filter: false,
    resizable: false,
    // AG Grid's default cell padding would offset the button inside this narrow column, so the
    // padding is dropped and the button is centred in the full cell instead.
    cellStyle: {
      paddingLeft: 0,
      paddingRight: 0,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
    },
    cellRenderer: (params: ICellRendererParams<ClaimRow>) => {
      const claim = params.data;
      // Detail rows are rendered full-width, so they never reach this renderer; the guard is for
      // the brief frame where AG Grid asks for a cell before the full-width swap.
      if (!claim || isDetailRow(claim)) return null;

      const expanded = isExpanded(claim);
      return (
        <button
          type="button"
          aria-expanded={expanded}
          aria-label={expanded ? "Collapse expense items" : "Expand expense items"}
          onClick={() => onToggle(claim)}
          className="flex size-6 items-center justify-center rounded-md border text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <ChevronRight
            className={cn("size-3.5 transition-transform", expanded && "rotate-90")}
          />
        </button>
      );
    },
  };
}
