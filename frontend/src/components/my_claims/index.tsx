"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { AgGridReact } from "ag-grid-react";
import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
  type ColDef,
  type ICellRendererParams,
  type IsFullWidthRowParams,
  type RowHeightParams,
} from "ag-grid-community";
import { ChevronDown, FileText, PlusCircle, Search } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { INITIAL_MULTI_ITEM_CLAIMS } from "@/data/claims";
import { CLAIM_STATUSES, CLAIM_STATUS_LABELS } from "@/lib/claimStatus";
import { cn } from "@/lib/utils";
import type { Claim } from "@/types";

import { buildColumnDefs } from "./columns";
import { ClaimDetailDialog } from "./ClaimDetailDialog";
import { ClaimItemsPanel } from "./ClaimItemsPanel";

ModuleRegistry.registerModules([AllCommunityModule]);

const ALL_STATUSES = "all-statuses";

const CLAIM_ROW_HEIGHT = 64;
const ESTIMATED_ITEM_HEIGHT = 72;
const ESTIMATED_PANEL_CHROME = 76;

/** A grid row is either a claim or the expanded item breakdown beneath it. */
type ClaimRow = { kind: "claim"; id: string; claim: Claim };
type DetailRow = { kind: "detail"; id: string; claim: Claim };
type GridRow = ClaimRow | DetailRow;

function MyClaims() {
  const router = useRouter();
  const [search, setSearch] = React.useState("");
  const [status, setStatus] = React.useState<string>(ALL_STATUSES);
  const [selectedClaim, setSelectedClaim] = React.useState<Claim | null>(null);
  const [isDetailOpen, setIsDetailOpen] = React.useState(false);
  const [expandedIds, setExpandedIds] = React.useState<string[]>([]);
  const gridRef = React.useRef<AgGridReact<GridRow> | null>(null);
  // Measured detail-panel heights, keyed by claim id.
  const panelHeights = React.useRef<Record<string, number>>({});

  // Panels report their true height after layout; push it into the grid so the
  // row resizes to fit instead of clipping into the next claim.
  const handlePanelMeasure = React.useCallback((claimId: string, height: number) => {
    if (panelHeights.current[claimId] === height) return;
    panelHeights.current[claimId] = height;
    gridRef.current?.api?.resetRowHeights();
  }, []);

  const handleView = React.useCallback((claim: Claim) => {
    setSelectedClaim(claim);
    setIsDetailOpen(true);
  }, []);

  const toggleExpanded = React.useCallback((claimId: string) => {
    setExpandedIds((current) =>
      current.includes(claimId)
        ? current.filter((id) => id !== claimId)
        : [...current, claimId]
    );
  }, []);

  const claims = React.useMemo(() => {
    const query = search.trim().toLowerCase();

    return INITIAL_MULTI_ITEM_CLAIMS.filter((claim) => {
      const matchesSearch =
        !query ||
        claim.claimNumber.toLowerCase().includes(query) ||
        claim.claimTitle.toLowerCase().includes(query) ||
        claim.employeeName.toLowerCase().includes(query);

      const matchesStatus = status === ALL_STATUSES || claim.status === status;

      return matchesSearch && matchesStatus;
    });
  }, [search, status]);

  // Flatten claims into grid rows, splicing a full-width detail row in after
  // each expanded claim.
  const rowData = React.useMemo<GridRow[]>(
    () =>
      claims.flatMap((claim) =>
        expandedIds.includes(claim.id)
          ? [
              { kind: "claim" as const, id: claim.id, claim },
              { kind: "detail" as const, id: `${claim.id}-detail`, claim },
            ]
          : [{ kind: "claim" as const, id: claim.id, claim }]
      ),
    [claims, expandedIds]
  );

  const columnDefs = React.useMemo<ColDef<GridRow>[]>(() => {
    const expanderColumn: ColDef<GridRow> = {
      colId: "expander",
      headerName: "",
      width: 60,
      minWidth: 60,
      maxWidth: 60,
      sortable: false,
      filter: false,
      resizable: false,
      cellRenderer: (params: ICellRendererParams<GridRow>) => {
        const row = params.data;
        if (!row || row.kind !== "claim") return null;
        const isExpanded = expandedIds.includes(row.claim.id);

        return (
          // pt matches the shared first-line offset in columns.tsx, less half the
          // button's overhang, so the chevron centers on the claim-title line.
          <div className="flex h-full flex-col items-start pt-[12px]">
            <button
              type="button"
              aria-expanded={isExpanded}
              aria-label={`${isExpanded ? "Hide" : "Show"} expense items for ${row.claim.claimNumber}`}
              onClick={() => toggleExpanded(row.claim.id)}
              className="flex size-7 items-center justify-center rounded-md border text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <ChevronDown
                className={cn("size-4 transition-transform", isExpanded && "rotate-180")}
              />
            </button>
          </div>
        );
      },
    };

    // The claim columns read `Claim`, but grid rows are the `GridRow` wrapper —
    // unwrap `.claim` before delegating to the shared column defs.
    const claimColumns = buildColumnDefs(handleView).map((col) => ({
      ...col,
      valueGetter: (params: { data?: GridRow }) => {
        const row = params.data;
        if (!row || row.kind !== "claim") return null;
        const original = col.valueGetter;
        if (typeof original === "function") {
          return original({ ...params, data: row.claim } as never);
        }
        return col.field ? row.claim[col.field as keyof Claim] : null;
      },
      cellRenderer: col.cellRenderer
        ? (params: ICellRendererParams<GridRow>) => {
            const row = params.data;
            if (!row || row.kind !== "claim") return null;
            const Renderer = col.cellRenderer as (p: unknown) => React.ReactNode;
            return Renderer({ ...params, data: row.claim });
          }
        : undefined,
    })) as ColDef<GridRow>[];

    return [expanderColumn, ...claimColumns];
  }, [expandedIds, handleView, toggleExpanded]);

  const defaultColDef = React.useMemo<ColDef>(
    () => ({
      resizable: true,
      sortable: true,
      filter: false,
    }),
    []
  );

  const isFullWidthRow = React.useCallback(
    (params: IsFullWidthRowParams) => (params.rowNode.data as GridRow)?.kind === "detail",
    []
  );

  const fullWidthCellRenderer = React.useCallback(
    (params: ICellRendererParams<GridRow>) => {
      const row = params.data;
      if (!row || row.kind !== "detail") return null;
      return (
        <ClaimItemsPanel
          claim={row.claim}
          onMeasure={(height) => handlePanelMeasure(row.claim.id, height)}
        />
      );
    },
    [handlePanelMeasure]
  );

  const getRowHeight = React.useCallback((params: RowHeightParams) => {
    const row = params.data as GridRow | undefined;
    if (row?.kind !== "detail") return CLAIM_ROW_HEIGHT;

    return (
      panelHeights.current[row.claim.id] ??
      ESTIMATED_PANEL_CHROME + row.claim.items.length * ESTIMATED_ITEM_HEIGHT
    );
  }, []);

  const getRowId = React.useCallback(
    (params: { data: GridRow }) => params.data.id,
    []
  );

  return (
    <div className="rounded-xl border bg-card shadow-sm">
      <div className="flex flex-col gap-4 p-6 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-4">
          <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-primary/10">
            <FileText className="size-6 text-primary" />
          </div>
          <div className="min-w-0">
            <h2 className="text-xl font-semibold text-foreground">My Expense Claims</h2>
            <p className="text-sm text-muted-foreground">
              Track every claim you&rsquo;ve raised. Expand one to see each expense item,
              why it cleared or needs review, and where the claim sits today.
            </p>
          </div>
        </div>
        <Button
          className="w-full sm:w-auto"
          onClick={() => router.push("/submit-expense")}
        >
          <PlusCircle />
          Submit New Expense
        </Button>
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
        <AgGridReact<GridRow>
          ref={gridRef}
          theme={themeQuartz}
          rowData={rowData}
          columnDefs={columnDefs}
          defaultColDef={defaultColDef}
          getRowId={getRowId}
          isFullWidthRow={isFullWidthRow}
          fullWidthCellRenderer={fullWidthCellRenderer}
          getRowHeight={getRowHeight}
          overlayNoRowsTemplate="No claims found matching your filter criteria."
          domLayout="normal"
          headerHeight={44}
          suppressCellFocus
        />
      </div>

      <ClaimDetailDialog
        claim={selectedClaim}
        open={isDetailOpen}
        onOpenChange={setIsDetailOpen}
      />
    </div>
  );
}

export default MyClaims;
