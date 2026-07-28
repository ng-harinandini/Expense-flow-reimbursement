"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { AgGridReact } from "ag-grid-react";
import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
  type ColDef,
} from "ag-grid-community";
import { PlusCircle, Search } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { INITIAL_CLAIMS } from "@/data/initialClaims";
import type { ClaimStatus, ExpenseCategory, ExpenseClaim } from "@/types";

import { buildColumnDefs, STATUS_LABELS } from "./columns";

ModuleRegistry.registerModules([AllCommunityModule]);

const CATEGORY_OPTIONS: ExpenseCategory[] = [
  "Meals",
  "Ground Transport",
  "Flights",
  "Lodging",
  "Client Entertainment",
  "Software & Subscriptions",
];

const STATUS_OPTIONS: ClaimStatus[] = [
  "Auto_Approved",
  "Manager_Review",
  "Finance_Review",
  "Flagged_Fraud",
  "Approved",
  "Disbursed",
];

const ALL_CATEGORIES = "all-categories";
const ALL_STATUSES = "all-statuses";

function MyClaims() {
  const router = useRouter();
  const [search, setSearch] = React.useState("");
  const [category, setCategory] = React.useState<string>(ALL_CATEGORIES);
  const [status, setStatus] = React.useState<string>(ALL_STATUSES);

  const columnDefs = React.useMemo<ColDef<ExpenseClaim>[]>(() => buildColumnDefs(), []);

  const rowData = React.useMemo(() => {
    const query = search.trim().toLowerCase();

    return INITIAL_CLAIMS.filter((claim) => {
      const matchesSearch =
        !query ||
        claim.claimNumber.toLowerCase().includes(query) ||
        claim.merchantVendor.toLowerCase().includes(query) ||
        claim.employeeName.toLowerCase().includes(query);

      const matchesCategory = category === ALL_CATEGORIES || claim.category === category;
      const matchesStatus = status === ALL_STATUSES || claim.status === status;

      return matchesSearch && matchesCategory && matchesStatus;
    });
  }, [search, category, status]);

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
        <div>
          <h2 className="text-xl font-semibold text-foreground">Expense Claims Console</h2>
          <p className="text-sm text-muted-foreground">
            Review claims, AI policy violations, and fraud risk scores
          </p>
        </div>
        <Button
          className="w-full sm:w-auto"
          onClick={() => router.push("/submit-expense")}
        >
          <PlusCircle />
          Submit New Expense
        </Button>
      </div>

      <div className="flex flex-col gap-3 px-6 pb-4 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by claim #, vendor, employee..."
            className="pl-9 text-foreground"
          />
        </div>

        <Select value={category} onValueChange={setCategory}>
          <SelectTrigger className="h-9 text-foreground sm:w-48">
            <SelectValue placeholder="All Categories" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_CATEGORIES}>All Categories</SelectItem>
            {CATEGORY_OPTIONS.map((option) => (
              <SelectItem key={option} value={option}>
                {option}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger className="h-9 text-foreground sm:w-48">
            <SelectValue placeholder="All Statuses" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_STATUSES}>All Statuses</SelectItem>
            {STATUS_OPTIONS.map((option) => (
              <SelectItem key={option} value={option}>
                {STATUS_LABELS[option]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="h-[560px] px-6 pb-6">
        <AgGridReact<ExpenseClaim>
          theme={themeQuartz}
          rowData={rowData}
          columnDefs={columnDefs}
          defaultColDef={defaultColDef}
          overlayNoRowsTemplate="No claims found matching your filter criteria."
          domLayout="normal"
          rowHeight={56}
          headerHeight={44}
        />
      </div>
    </div>
  );
}

export default MyClaims;
