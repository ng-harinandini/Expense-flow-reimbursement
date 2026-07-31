"use client";

import * as React from "react";

import { Button } from "@/components/ui/Button";
import {
  CLAIM_STATUS_LABELS,
  getClaimStatusExplanation,
  itemNeedsReview,
} from "@/lib/claimStatus";
import type { Claim, ClaimExpenseItem } from "@/types";

import { formatCurrency, ItemStatusBadge, ItemStatusDot } from "./columns";

interface ClaimItemsPanelProps {
  claim: Claim;
  onReviewItem?: (claim: Claim, item: ClaimExpenseItem) => void;
  /**
   * Reports the panel's rendered height so the grid row can be sized to fit.
   * The explanation text wraps to a variable number of lines depending on
   * column width, so the height cannot be computed from the item count alone.
   */
  onMeasure?: (height: number) => void;
}

/**
 * Expanded detail for a claim row: why the claim landed on its status, then one
 * card per expense item. Items are read-only here — only items that still need
 * a decision get an action, so there is no per-item "View".
 */
export function ClaimItemsPanel({ claim, onReviewItem, onMeasure }: ClaimItemsPanelProps) {
  const panelRef = React.useRef<HTMLDivElement | null>(null);

  React.useLayoutEffect(() => {
    const node = panelRef.current;
    if (!node || !onMeasure) return;

    onMeasure(node.offsetHeight);

    const observer = new ResizeObserver(() => onMeasure(node.offsetHeight));
    observer.observe(node);
    return () => observer.disconnect();
  }, [onMeasure]);

  return (
    <div ref={panelRef} className="space-y-3 border-t bg-muted/30 px-6 py-4">
      <p className="text-sm">
        <span className="font-semibold text-foreground">
          Why &ldquo;{CLAIM_STATUS_LABELS[claim.status]}&rdquo;?
        </span>{" "}
        <span className="text-muted-foreground">
          {getClaimStatusExplanation(claim.status, claim.items)}
        </span>
      </p>

      <div className="space-y-2">
        {claim.items.map((item) => {
          const needsReview = itemNeedsReview(item.status);

          return (
            <div
              key={item.id}
              className="flex items-start gap-3 rounded-lg border bg-card p-3"
            >
              <span className="mt-1.5 flex shrink-0">
                <ItemStatusDot status={item.status} />
              </span>

              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-foreground">{item.merchantVendor}</p>
                <p className="text-xs text-muted-foreground">
                  {item.category}
                  {item.statusReason ? ` · ${item.statusReason}` : ""}
                </p>
              </div>

              <p className="shrink-0 text-sm font-semibold text-foreground">
                {formatCurrency(item.amount, item.currency)}
              </p>

              <div className="shrink-0">
                <ItemStatusBadge status={item.status} />
              </div>

              <div className="flex w-20 shrink-0 justify-end">
                {needsReview && onReviewItem && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => onReviewItem(claim, item)}
                  >
                    Review
                  </Button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
