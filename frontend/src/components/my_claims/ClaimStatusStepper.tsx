"use client";

import * as React from "react";
import {
  AlertTriangle,
  BadgeCheck,
  Check,
  FileText,
  ScanLine,
  UserCircle,
  Wallet,
  X,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";
import type { ClaimStatus, ExpenseClaim } from "@/types";

import { formatDate } from "./columns";

type StepKey = "submitted" | "aiScanned" | "managerReview" | "financeReview" | "disbursed";

interface StepDefinition {
  key: StepKey;
  label: string;
  icon: LucideIcon;
}

const STEPS: StepDefinition[] = [
  { key: "submitted", label: "Submitted", icon: FileText },
  { key: "aiScanned", label: "AI scanned", icon: ScanLine },
  { key: "managerReview", label: "Manager review", icon: UserCircle },
  { key: "financeReview", label: "Finance review", icon: Wallet },
  { key: "disbursed", label: "Disbursed", icon: BadgeCheck },
];

const TERMINAL_STEP_INDEX_BY_STATUS: Record<ClaimStatus, number> = {
  Draft: -1,
  Submitted: 0,
  Processing_AI: 1,
  Auto_Approved: 3,
  Manager_Review: 2,
  Finance_Review: 3,
  Approved: 3,
  Rejected: 3,
  Disbursed: 4,
  Flagged_Fraud: 2,
};

export function getCurrentStepIndex(status: ClaimStatus): number {
  return TERMINAL_STEP_INDEX_BY_STATUS[status] ?? 0;
}

const STEP_STATUS_LABEL_OVERRIDE: Partial<Record<ClaimStatus, string>> = {
  Approved: "Approved",
  Auto_Approved: "Approved",
  Rejected: "Rejected",
  Flagged_Fraud: "Flagged for review",
};

interface StepVisualOverride {
  icon: LucideIcon;
  circleClass: string;
  textClass: string;
}

// Some statuses are outcomes rather than pipeline stages (rejected, flagged,
// approved-ahead-of-disbursement) — swap the current step's icon/color to
// reflect the actual outcome instead of the generic pipeline stage icon.
function getCurrentStepOverride(status: ClaimStatus): StepVisualOverride | null {
  switch (status) {
    case "Approved":
    case "Auto_Approved":
      return { icon: Check, circleClass: "bg-emerald-600 text-white", textClass: "text-emerald-600" };
    case "Rejected":
      return { icon: X, circleClass: "bg-destructive text-white", textClass: "text-destructive" };
    case "Flagged_Fraud":
      return { icon: AlertTriangle, circleClass: "bg-destructive text-white", textClass: "text-destructive" };
    default:
      return null;
  }
}

function getStepTimestamp(claim: ExpenseClaim, key: StepKey): string | undefined {
  const match = claim.workflowHistory.find((entry) => {
    if (key === "submitted") return entry.stepName === "Submit Claim";
    if (key === "aiScanned")
      return entry.stepName === "AI OCR Extraction" || entry.stepName === "Policy Validation";
    if (key === "financeReview") return entry.stepName === "Finance Review";
    return false;
  });
  return match?.timestamp;
}

function StepRow({
  step,
  index,
  currentIndex,
  claim,
  isLast,
}: {
  step: StepDefinition;
  index: number;
  currentIndex: number;
  claim: ExpenseClaim;
  isLast: boolean;
}) {
  const isDone = index < currentIndex;
  const isCurrent = index === currentIndex;
  const timestamp = getStepTimestamp(claim, step.key);
  const override = isCurrent ? getCurrentStepOverride(claim.status) : null;
  const Icon = override?.icon ?? step.icon;

  return (
    <div className="flex gap-3">
      <div className="flex flex-col items-center">
        <div
          className={cn(
            "flex size-6 shrink-0 items-center justify-center rounded-full",
            isDone && "bg-emerald-600 text-white",
            isCurrent && (override?.circleClass ?? "bg-secondary text-secondary-foreground"),
            !isDone && !isCurrent && "bg-muted text-muted-foreground"
          )}
        >
          <Icon className="size-3.5" />
        </div>
        {!isLast && (
          <div
            className={cn(
              "w-px flex-1 min-h-5",
              isDone ? "bg-emerald-600" : "bg-border"
            )}
          />
        )}
      </div>
      <div className="pb-5">
        <p
          className={cn(
            "text-sm",
            isCurrent
              ? cn("font-semibold", override?.textClass ?? "text-secondary")
              : "font-medium text-foreground",
            !isDone && !isCurrent && "text-muted-foreground"
          )}
        >
          {step.label}
        </p>
        <p className="text-xs text-muted-foreground">
          {isCurrent && !timestamp
            ? (STEP_STATUS_LABEL_OVERRIDE[claim.status] ?? "In progress")
            : timestamp
              ? formatDate(timestamp)
              : "Pending"}
        </p>
      </div>
    </div>
  );
}

export function ClaimStatusStepper({
  claim,
  className,
}: {
  claim: ExpenseClaim;
  className?: string;
}) {
  const currentIndex = getCurrentStepIndex(claim.status);

  return (
    <div className={className}>
      {STEPS.map((step, index) => (
        <StepRow
          key={step.key}
          step={step}
          index={index}
          currentIndex={currentIndex}
          claim={claim}
          isLast={index === STEPS.length - 1}
        />
      ))}
    </div>
  );
}
