"use client";

import { AgGridReact } from "ag-grid-react";
import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
} from "ag-grid-community";
import type { AgGridReactProps } from "ag-grid-react";

// Registered once for the whole app instead of once per grid page. `registerModules` is
// idempotent, but repeating it in five route bundles was what pulled the entire ag-grid
// runtime into each of those bundles eagerly.
ModuleRegistry.registerModules([AllCommunityModule]);

/**
 * The real grid. Nothing imports this directly — `DataGrid` loads it on demand so that
 * `ag-grid-community` lands in its own async chunk rather than in every route's initial
 * JavaScript. See `./DataGrid.tsx`.
 */
export default function DataGridImpl(props: AgGridReactProps) {
  return <AgGridReact theme={themeQuartz} {...props} />;
}
