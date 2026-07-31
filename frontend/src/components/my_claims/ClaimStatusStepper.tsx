"use client";

import * as React from "react";
import {
  AlertTriangle,
  BadgeCheck,
  Check,
  FileText,
  UserCircle,
  Wallet,
  X,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";
import type { ClaimStatus, UserRole, WorkflowStepLog } from "@/types";

import { formatDate } from "./columns";

interface StepperClaim {
  status: ClaimStatus;
  workflowHistory: WorkflowStepLog[];
}

const ROLE_LABELS: Record<UserRole, string> = {
  employee: "Employee",
  manager: "Manager",
  finance: "Finance",
  admin: "Admin",
  auditor: "Auditor",
};

const ROLE_BADGE_CLASSES: Record<UserRole, string> = {
  employee: "bg-emerald-500/15 text-emerald-700",
  manager: "bg-secondary/15 text-secondary",
  finance: "bg-blue-500/15 text-blue-700",
  admin: "bg-amber-500/15 text-amber-700",
  auditor: "bg-muted text-muted-foreground",
};

type StepKey = "submitted" | "managerReview" | "financeReview" | "disbursed";

interface StepDefinition {
  key: StepKey;
  label: string;
  icon: LucideIcon;
}

const STEPS: StepDefinition[] = [
  { key: "submitted", label: "Submitted", icon: FileText },
  { key: "managerReview", label: "Manager review", icon: UserCircle },
  { key: "financeReview", label: "Finance review", icon: Wallet },
  { key: "disbursed", label: "Disbursed", icon: BadgeCheck },
];

const TERMINAL_STEP_INDEX_BY_STATUS: Record<ClaimStatus, number> = {
  Draft: -1,
  Submitted: 0,
  // AI scanning is invisible in the stepper now — the claim just stays on
  // "Submitted" until manager review actually begins.
  Processing_AI: 0,
  Auto_Approved: 2,
  Manager_Review: 1,
  Finance_Review: 2,
  Approved: 2,
  Rejected: 2,
  Disbursed: 3,
  Flagged_Fraud: 1,
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

function findWorkflowEntry(claim: StepperClaim, key: StepKey): WorkflowStepLog | undefined {
  return claim.workflowHistory.find((entry) => {
    if (key === "submitted") return entry.stepName === "Submit Claim";
    if (key === "financeReview") return entry.stepName === "Finance Review";
    return false;
  });
}

interface StepActor {
  name: string;
  role: UserRole;
}

// Manager review has no dedicated workflowHistory entry in the seed data (only
// Submit Claim / Finance Review are logged), so its actor comes from the
// employee's assigned manager instead of the log.
function getStepActor(
  step: StepDefinition,
  workflowEntry: WorkflowStepLog | undefined,
  manager: string | undefined
): StepActor | undefined {
  if (step.key === "managerReview") {
    return manager ? { name: manager, role: "manager" } : undefined;
  }
  return workflowEntry ? { name: workflowEntry.actorName, role: workflowEntry.actorRole } : undefined;
}

function StepRow({
  step,
  index,
  currentIndex,
  claim,
  manager,
  isLast,
}: {
  step: StepDefinition;
  index: number;
  currentIndex: number;
  claim: StepperClaim;
  manager: string | undefined;
  isLast: boolean;
}) {
  const isDone = index < currentIndex;
  const isCurrent = index === currentIndex;
  const workflowEntry = findWorkflowEntry(claim, step.key);
  const timestamp = workflowEntry?.timestamp;
  const override = isCurrent ? getCurrentStepOverride(claim.status) : null;
  const Icon = override?.icon ?? step.icon;
  // Only reveal who acted on a step once it's been reached — pending steps
  // haven't necessarily been assigned to the person who'll end up handling them.
  const actor = (isDone || isCurrent) ? getStepActor(step, workflowEntry, manager) : undefined;

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
        {actor && (
          <>
            <p className="mt-1.5 text-xs text-muted-foreground">by {actor.name}</p>
            <span
              className={cn(
                "mt-1 inline-block rounded-full px-2 py-0.5 text-[11px] font-medium",
                ROLE_BADGE_CLASSES[actor.role]
              )}
            >
              {ROLE_LABELS[actor.role]}
            </span>
          </>
        )}
      </div>
    </div>
  );
}

export function ClaimStatusStepper({
  claim,
  manager,
  className,
}: {
  claim: StepperClaim;
  manager?: string;
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
          manager={manager}
          isLast={index === STEPS.length - 1}
        />
      ))}
    </div>
  );
}
