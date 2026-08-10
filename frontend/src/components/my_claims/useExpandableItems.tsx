"use client";

import * as React from "react";
import type { ColDef, RowHeightParams } from "ag-grid-community";

import type { Claim } from "@/types";

import {
  ExpenseItemsDetailRow,
  buildExpandColumn,
  isDetailRow,
  type ClaimRow,
} from "./ExpenseItemChips";

/**
 * Detail row height: the status-reason line plus one row of chips, growing a row taller for each
 * wrapped line of chips.
 */
const DETAIL_ROW_BASE_HEIGHT = 84;
const DETAIL_ROW_LINE_HEIGHT = 40;
/** Roughly how many chips fit on one line before wrapping — drives the detail row's height. */
const CHIPS_PER_LINE = 4;

/**
 * Wires up click-to-expand expense-item chips for a claims grid.
 *
 * Returns the props a grid needs: `rowData` with detail rows spliced in after their claim,
 * `columnDefs` with the chevron column prepended, and the full-width row config that renders
 * chips instead of cells.
 */
export function useExpandableItems(
  claims: Claim[],
  columnDefs: ColDef<Claim>[],
  rowHeight: number,
) {
  const [expandedIds, setExpandedIds] = React.useState<Set<string>>(new Set());

  const toggle = React.useCallback((claim: Claim) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(claim.id)) {
        next.delete(claim.id);
      } else {
        next.add(claim.id);
      }
      return next;
    });
  }, []);

  const isExpanded = React.useCallback(
    (claim: Claim) => expandedIds.has(claim.id),
    [expandedIds],
  );

  // A claim filtered out of the grid shouldn't stay expanded if it comes back later.
  const visibleIds = React.useMemo(
    () => new Set(claims.map((claim) => claim.id)),
    [claims],
  );
  React.useEffect(() => {
    setExpandedIds((prev) => {
      const next = new Set([...prev].filter((id) => visibleIds.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [visibleIds]);

  const rowData = React.useMemo<ClaimRow[]>(() => {
    const rows: ClaimRow[] = [];
    for (const claim of claims) {
      rows.push(claim);
      if (expandedIds.has(claim.id)) {
        // Detail rows carry the parent's fields so any column's valueGetter stays safe, plus the
        // `__detailOf` marker that flags them for full-width rendering.
        rows.push({ ...claim, id: `${claim.id}__detail`, __detailOf: claim });
      }
    }
    return rows;
  }, [claims, expandedIds]);

  const columnDefsWithExpand = React.useMemo<ColDef<ClaimRow>[]>(
    () => [
      buildExpandColumn(isExpanded, toggle),
      ...(columnDefs as ColDef<ClaimRow>[]),
    ],
    [columnDefs, isExpanded, toggle],
  );

  const getRowHeight = React.useCallback(
    (params: RowHeightParams<ClaimRow>) => {
      const detail = params.data?.__detailOf;
      if (!detail) return rowHeight;
      const lines = Math.max(1, Math.ceil(detail.items.length / CHIPS_PER_LINE));
      return DETAIL_ROW_BASE_HEIGHT + (lines - 1) * DETAIL_ROW_LINE_HEIGHT;
    },
    [rowHeight],
  );

  const gridProps = React.useMemo(
    () => ({
      rowData,
      columnDefs: columnDefsWithExpand,
      getRowHeight,
      // Stable ids keep expansion attached to the right claim across sorts and filters.
      getRowId: (params: { data: ClaimRow }) => params.data.id,
      isFullWidthRow: (params: { rowNode: { data?: ClaimRow } }) =>
        isDetailRow(params.rowNode.data),
      fullWidthCellRenderer: (params: { data?: ClaimRow }) =>
        params.data?.__detailOf ? (
          <ExpenseItemsDetailRow claim={params.data.__detailOf} />
        ) : null,
    }),
    [rowData, columnDefsWithExpand, getRowHeight],
  );

  return gridProps;
}
