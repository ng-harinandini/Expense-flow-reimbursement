"use client";

import * as React from "react";
import type { ColDef } from "ag-grid-community";
import { PencilLine, ShieldCheck, Sparkles } from "lucide-react";

import { DataGrid } from "@/components/shared/DataGrid";
import { Button } from "@/components/ui/Button";
import { usePolicyRulesQuery, usePutPolicyRulesMutation } from "@/api/policyRules";
import type { AdminPolicyRule } from "@/types";

import { buildColumnDefs } from "./columns";
import { nextRuleId, formValuesToPolicyRule } from "./helpers";
import { AddPolicyRuleDialog } from "./AddPolicyRuleDialog";
import { AddPolicyRuleWithAIDialog } from "./AddPolicyRuleWithAIDialog";
import { DeletePolicyRuleDialog } from "./DeletePolicyRuleDialog";
import type { PolicyRuleFormValues } from "./policyRuleSchema";

function PolicyGuidelines() {
  const { data: rules = [], isLoading } = usePolicyRulesQuery();
  const putPolicyRules = usePutPolicyRulesMutation();

  const [isManualOpen, setIsManualOpen] = React.useState(false);
  const [isAIOpen, setIsAIOpen] = React.useState(false);
  const [editingRule, setEditingRule] = React.useState<AdminPolicyRule | null>(null);
  const [deletingRule, setDeletingRule] = React.useState<AdminPolicyRule | null>(null);
  const [isDeleteOpen, setIsDeleteOpen] = React.useState(false);

  const handleAdd = React.useCallback(() => {
    setEditingRule(null);
    setIsManualOpen(true);
  }, []);

  const handleEdit = React.useCallback((rule: AdminPolicyRule) => {
    setEditingRule(rule);
    setIsManualOpen(true);
  }, []);

  const handleDelete = React.useCallback((rule: AdminPolicyRule) => {
    setDeletingRule(rule);
    setIsDeleteOpen(true);
  }, []);

  // The backend only exposes `PUT /policy-rules` — a full-ruleset publish that versions every
  // rule present in the payload and retires any active rule whose code is missing from it. So
  // create, update, and delete all build the full desired list here, then send it in one call.
  const publishRules = React.useCallback(
    (nextRules: AdminPolicyRule[]) => putPolicyRules.mutateAsync(nextRules),
    [putPolicyRules]
  );

  const handleSave = React.useCallback(
    async (values: PolicyRuleFormValues) => {
      if (editingRule) {
        const updated = formValuesToPolicyRule(editingRule.id, values, editingRule.code);
        await publishRules(rules.map((r) => (r.id === editingRule.id ? updated : r)));
        return;
      }

      const created = formValuesToPolicyRule(nextRuleId(rules), values);
      await publishRules([...rules, created]);
    },
    [editingRule, rules, publishRules]
  );

  const handleConfirmDelete = React.useCallback(
    async (rule: AdminPolicyRule) => {
      await publishRules(rules.filter((r) => r.id !== rule.id));
    },
    [rules, publishRules]
  );

  const columnDefs = React.useMemo<ColDef<AdminPolicyRule>[]>(
    () => buildColumnDefs(handleEdit, handleDelete),
    [handleEdit, handleDelete]
  );

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
          <Button variant="outline" onClick={handleAdd}>
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
          loading={isLoading}
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

      <AddPolicyRuleDialog
        rule={editingRule}
        open={isManualOpen}
        onOpenChange={setIsManualOpen}
        onSave={handleSave}
      />

      <DeletePolicyRuleDialog
        rule={deletingRule}
        open={isDeleteOpen}
        onOpenChange={setIsDeleteOpen}
        onConfirm={handleConfirmDelete}
      />

      <AddPolicyRuleWithAIDialog
        open={isAIOpen}
        onOpenChange={setIsAIOpen}
        onSaveRule={(values) => {
          void publishRules([...rules, formValuesToPolicyRule(nextRuleId(rules), values)]);
        }}
      />
    </div>
  );
}

export default PolicyGuidelines;
