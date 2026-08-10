"use client";

import dynamic from "next/dynamic";
import type { AgGridReactProps } from "ag-grid-react";

/**
 * Lazily-loaded ag-grid.
 *
 * `ag-grid-community` is ~7 MB unminified. Imported statically it was inlined into the
 * initial bundle of every route that shows a table (my-claims, approvals, finance-approvals,
 * organisation, policy-guidelines), so each of those routes shipped its own copy — around
 * 10 MB of JavaScript to download, parse and execute before the page could paint, and again
 * for the next table route because dev serves chunks with `no-store`.
 *
 * Loading it through `next/dynamic` moves the grid into one shared async chunk that is
 * fetched only when a grid actually renders, and reused by every later table route.
 *
 * `ssr: false` because ag-grid touches `document` while measuring, and the grid renders no
 * meaningful server markup anyway — every table on this app is populated from a client query.
 */
const LazyDataGrid = dynamic(() => import("./DataGridImpl"), {
  ssr: false,
  loading: () => (
    <div
      className="h-full w-full animate-pulse rounded-lg bg-muted/40"
      aria-label="Loading table"
    />
  ),
});

export function DataGrid<TData>(props: AgGridReactProps<TData>) {
  return <LazyDataGrid {...(props as AgGridReactProps)} />;
}
