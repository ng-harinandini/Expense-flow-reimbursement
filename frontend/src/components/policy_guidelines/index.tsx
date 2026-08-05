"use client";

import * as React from "react";
import type { ColDef } from "ag-grid-community";
import { PencilLine, ShieldCheck, Sparkles } from "lucide-react";

import { DataGrid } from "@/components/shared/DataGrid";
import { Button } from "@/components/ui/Button";
import { INITIAL_POLICY_RULES } from "@/data/policyRules";
import type { AdminPolicyRule } from "@/types";

import { buildColumnDefs } from "./columns";
import { nextRuleId, formValuesToPolicyRule } from "./helpers";
import { AddPolicyRuleDialog } from "./AddPolicyRuleDialog";
import { AddPolicyRuleWithAIDialog } from "./AddPolicyRuleWithAIDialog";
import type { PolicyRuleFormValues } from "./policyRuleSchema";

function PolicyGuidelines() {
  const [rules, setRules] = React.useState<AdminPolicyRule[]>(INITIAL_POLICY_RULES);
  const [isManualOpen, setIsManualOpen] = React.useState(false);
  const [isAIOpen, setIsAIOpen] = React.useState(false);

  // TODO: replace with POST /api/policy-rules once the endpoint exists.
  const addRule = React.useCallback((values: PolicyRuleFormValues) => {
    setRules((current) => [...current, formValuesToPolicyRule(nextRuleId(current), values)]);
  }, []);

  const columnDefs = React.useMemo<ColDef<AdminPolicyRule>[]>(() => buildColumnDefs(), []);

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
        <div className="flex items-center gap-4">
          <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-primary/10">
            <ShieldCheck className="size-6 text-primary" />
          </div>
          <div className="min-w-0">
            <h2 className="text-xl font-semibold text-foreground">Policy Guidelines</h2>
            <p className="text-sm text-muted-foreground">
              Manage the expense policy rules enforced during claim review
            </p>
          </div>
        </div>

        <div className="flex flex-col gap-2 sm:flex-row">
          <Button variant="outline" onClick={() => setIsManualOpen(true)}>
            <PencilLine />
            Add Manually
          </Button>
          <Button onClick={() => setIsAIOpen(true)}>
            <Sparkles />
            Add with AI
          </Button>
        </div>
      </div>

      <div className="h-[560px] px-6 pb-6">
        <DataGrid<AdminPolicyRule>
          rowData={rules}
          columnDefs={columnDefs}
          defaultColDef={defaultColDef}
          getRowId={(params) => String(params.data.id)}
          overlayNoRowsTemplate="No policy rules yet."
          domLayout="normal"
          rowHeight={56}
          headerHeight={44}
          suppressCellFocus
        />
      </div>

      <AddPolicyRuleDialog open={isManualOpen} onOpenChange={setIsManualOpen} onSave={addRule} />

      <AddPolicyRuleWithAIDialog
        open={isAIOpen}
        onOpenChange={setIsAIOpen}
        onSaveRule={addRule}
      />
    </div>
  );
}

export default PolicyGuidelines;
