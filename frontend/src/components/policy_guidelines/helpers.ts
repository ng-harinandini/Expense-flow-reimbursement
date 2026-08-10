import { EMPLOYEE_GRADES } from "@/components/organisation/helpers";
import type { AdminPolicyRule } from "@/types";
import type { PolicyRuleFormValues } from "./policyRuleSchema";


export const POLICY_RULE_GRADES: string[] = [
  "All",
  ...EMPLOYEE_GRADES,
  "Manager+",
];

/** Units a rule's max amount can be expressed per, drawn from the existing ruleset. */
export const POLICY_RULE_UNITS: string[] = ["day", "night", "trip", "event", "month", "year"];

/** Next free sequential id, based on the highest id currently in use. */
export function nextRuleId(rules: AdminPolicyRule[]) {
  return rules.reduce((max, rule) => Math.max(max, rule.id), 0) + 1;
}

export function formatMaxAmount(rule: Pick<AdminPolicyRule, "maxAmount" | "maxAmountUnit">) {
  return `$${rule.maxAmount} / ${rule.maxAmountUnit}`;
}

export function formatAutoApproveLimit(autoApproveLimit: number | null) {
  return autoApproveLimit === null ? "—" : `$${autoApproveLimit}`;
}

export function formatReceiptThreshold(requiresReceiptAbove: number) {
  return `$${requiresReceiptAbove}`;
}

export function formatEffectiveFrom(effectiveFrom: string) {
  const date = new Date(effectiveFrom);
  if (Number.isNaN(date.getTime())) return effectiveFrom;
  return date.toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "2-digit",
  });
}

export function policyRuleToFormValues(rule: AdminPolicyRule): PolicyRuleFormValues {
  return {
    category: rule.category,
    gradeApplicable: rule.gradeApplicable,
    maxAmount: rule.maxAmount,
    maxAmountUnit: rule.maxAmountUnit,
    autoApproveLimit: rule.autoApproveLimit ?? undefined,
    requiresReceiptAbove: rule.requiresReceiptAbove,
    effectiveFrom: rule.effectiveFrom,
  };
}

export function formValuesToPolicyRule(
  id: number,
  values: PolicyRuleFormValues,
  code?: string
): AdminPolicyRule {
  return {
    id,
    code,
    category: values.category as AdminPolicyRule["category"],
    gradeApplicable: values.gradeApplicable,
    maxAmount: values.maxAmount,
    maxAmountUnit: values.maxAmountUnit,
    autoApproveLimit: values.autoApproveLimit ?? null,
    requiresReceiptAbove: values.requiresReceiptAbove,
    effectiveFrom: values.effectiveFrom,
  };
}

/** Sample "documents" the mock AI extraction can return, cycled through on each upload. */
const MOCK_EXTRACTION_POOL: PolicyRuleFormValues[][] = [
  [
    {
      category: "Software & Subscriptions",
      gradeApplicable: "All (role-relevant tools only)",
      maxAmount: 300,
      maxAmountUnit: "year",
      autoApproveLimit: 100,
      requiresReceiptAbove: 0,
      effectiveFrom: "2026-01-01",
    },
    {
      category: "Team Events",
      gradeApplicable: "Manager+",
      maxAmount: 300,
      maxAmountUnit: "event",
      autoApproveLimit: undefined,
      requiresReceiptAbove: 0,
      effectiveFrom: "2026-01-01",
    },
    {
      category: "Health & Wellness",
      gradeApplicable: "All",
      maxAmount: 50,
      maxAmountUnit: "month",
      autoApproveLimit: 50,
      requiresReceiptAbove: 0,
      effectiveFrom: "2026-01-01",
    },
  ],
  [
    {
      category: "Training & Professional Dev",
      gradeApplicable: "All (with manager pre-approval)",
      maxAmount: 2000,
      maxAmountUnit: "year",
      autoApproveLimit: undefined,
      requiresReceiptAbove: 0,
      effectiveFrom: "2026-01-01",
    },
    {
      category: "Communications & Connectivity",
      gradeApplicable: "All / Remote roles",
      maxAmount: 50,
      maxAmountUnit: "month",
      autoApproveLimit: 50,
      requiresReceiptAbove: 0,
      effectiveFrom: "2026-01-01",
    },
  ],
];

let mockExtractionIndex = 0;

/**
 * Stands in for the real "upload a policy document, get structured rules
 * back" API call. Cycles through a small pool of canned extractions so
 * repeated uploads during a demo don't always show the same result.
 */
export function mockExtractPolicyRulesFromFile(_file: File): Promise<PolicyRuleFormValues[]> {
  const batch = MOCK_EXTRACTION_POOL[mockExtractionIndex % MOCK_EXTRACTION_POOL.length];
  mockExtractionIndex += 1;

  return new Promise((resolve) => {
    setTimeout(() => resolve(batch), 1800);
  });
}
