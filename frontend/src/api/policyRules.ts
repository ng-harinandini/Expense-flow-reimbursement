import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiRequest } from "./client";
import type { AdminPolicyRule, ExpenseCategory } from "@/types";


export interface PolicyRuleApiShape {
  category: string;
  maxAmountUSD?: number | string | null;
  autoApproveLimitUSD?: number | null;
  receiptRequiredAboveUSD?: number | null;
  requiresPreApproval?: boolean;
  gradeTier?: string | null;
  specialRules?: string[];
  code?: string | null;
  name?: string | null;
  description?: string | null;
  priority?: number | null;
  isActive?: boolean;
  effectiveDate?: string | null;
  [key: string]: unknown;
}

export const POLICY_RULES_QUERY_KEY = ["policy-rules"] as const;

/** Marker prefix inside `specialRules` carrying the row's unit — there's no dedicated wire field. */
const UNIT_PREFIX = "__unit:";
/** Marker prefix inside `specialRules` carrying the row's effective-from date for round-tripping. */
const EFFECTIVE_FROM_PREFIX = "__effectiveFrom:";

function slugifyCode(category: string, gradeApplicable: string): string {
  const slug = `${category}_${gradeApplicable}`
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_|_$/g, "");
  return `${slug}_RULE`;
}

/** Maps one backend rule into the grid's row shape, recovering the unit/id we smuggled in. */
function mapPolicyRule(raw: PolicyRuleApiShape, index: number): AdminPolicyRule {
  const specialRules = raw.specialRules ?? [];
  const unitEntry = specialRules.find((r) => r.startsWith(UNIT_PREFIX));
  const effectiveFromEntry = specialRules.find((r) => r.startsWith(EFFECTIVE_FROM_PREFIX));

  const maxAmount =
    typeof raw.maxAmountUSD === "number"
      ? raw.maxAmountUSD
      : Number.parseFloat(String(raw.maxAmountUSD ?? "0")) || 0;

  return {
    id: index + 1,
    code: raw.code ?? slugifyCode(raw.category, raw.gradeTier ?? "All Staff"),
    category: raw.category as ExpenseCategory,
    gradeApplicable: raw.gradeTier ?? "All Staff",
    maxAmount,
    maxAmountUnit: unitEntry ? unitEntry.slice(UNIT_PREFIX.length) : "month",
    autoApproveLimit: raw.autoApproveLimitUSD ?? null,
    requiresReceiptAbove: raw.receiptRequiredAboveUSD ?? 0,
    effectiveFrom: effectiveFromEntry
      ? effectiveFromEntry.slice(EFFECTIVE_FROM_PREFIX.length)
      : raw.effectiveDate ?? "",
    description: raw.description ?? null,
    isActive: raw.isActive ?? true,
  };
}

/** Maps one grid row back into the wire shape `PUT /policy-rules` expects. */
export function policyRuleToApiShape(rule: AdminPolicyRule): PolicyRuleApiShape {
  const specialRules = [
    `${UNIT_PREFIX}${rule.maxAmountUnit}`,
    `${EFFECTIVE_FROM_PREFIX}${rule.effectiveFrom}`,
  ];

  return {
    code: rule.code ?? slugifyCode(rule.category, rule.gradeApplicable),
    category: rule.category,
    gradeTier: rule.gradeApplicable,
    maxAmountUSD: rule.maxAmount,
    autoApproveLimitUSD: rule.autoApproveLimit,
    receiptRequiredAboveUSD: rule.requiresReceiptAbove,
    effectiveDate: rule.effectiveFrom || undefined,
    specialRules,
    description: rule.description || undefined,
    isActive: rule.isActive,
  };
}

export function getPolicyRules(): Promise<AdminPolicyRule[]> {
  return apiRequest<PolicyRuleApiShape[]>("/policy-rules").then((list) => list.map(mapPolicyRule));
}

export function usePolicyRulesQuery() {
  return useQuery({
    queryKey: POLICY_RULES_QUERY_KEY,
    queryFn: getPolicyRules,
  });
}


export function putPolicyRules(rules: AdminPolicyRule[]): Promise<AdminPolicyRule[]> {
  const body = rules.map(policyRuleToApiShape);

  return apiRequest<{ status: string; count: number; rules: PolicyRuleApiShape[] }>(
    "/policy-rules",
    {
      method: "PUT",
      body: JSON.stringify(body),
      headers: { "Content-Type": "application/json" },
    }
  ).then((res) => res.rules.map(mapPolicyRule));
}

export function usePutPolicyRulesMutation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: putPolicyRules,
    onSuccess: (rules) => {
      queryClient.setQueryData(POLICY_RULES_QUERY_KEY, rules);
    },
  });
}
