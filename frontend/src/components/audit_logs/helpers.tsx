import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { AuditTrailClaim, AuditTrailEvent, ClaimStatus } from "@/types";

const STATUS_LABELS: Record<ClaimStatus, string> = {
  Draft: "Draft",
  Submitted: "Submitted",
  Processing_AI: "Processing AI",
  Auto_Approved: "Auto Approved",
  Manager_Review: "Manager Review",
  Finance_Review: "Finance Review",
  Approved: "Approved",
  Rejected: "Rejected",
  Disbursed: "Disbursed",
  Flagged_Fraud: "Flagged Fraud",
};

const STATUS_BADGE_CLASSES: Record<ClaimStatus, string> = {
  Draft: "bg-muted text-muted-foreground border-transparent",
  Submitted: "bg-emerald-500/15 text-emerald-600 border-transparent",
  Processing_AI: "bg-blue-500/15 text-blue-600 border-transparent",
  Auto_Approved: "bg-emerald-500/15 text-emerald-600 border-transparent",
  Manager_Review: "bg-amber-500/15 text-amber-600 border-transparent",
  Finance_Review: "bg-amber-500/15 text-amber-600 border-transparent",
  Approved: "bg-emerald-500/15 text-emerald-600 border-transparent",
  Rejected: "bg-destructive/15 text-destructive border-transparent",
  Disbursed: "bg-secondary/15 text-secondary border-transparent",
  Flagged_Fraud: "bg-destructive/15 text-destructive border-transparent",
};

/** Status pill for a claim's audit trail row; appends the risk score for flagged claims. */
export function AuditStatusBadge({ claim }: { claim: Pick<AuditTrailClaim, "status" | "riskScore"> }) {
  const label =
    claim.status === "Flagged_Fraud" && claim.riskScore != null
      ? `${STATUS_LABELS[claim.status]} · Risk ${claim.riskScore}`
      : STATUS_LABELS[claim.status];

  return (
    <Badge className={cn("font-medium", STATUS_BADGE_CLASSES[claim.status])}>{label}</Badge>
  );
}

/** Unique actor names in first-seen order, e.g. for the "Actors Involved" column. */
export function actorsInvolved(events: AuditTrailEvent[]): string[] {
  return Array.from(new Set(events.map((event) => event.actor)));
}

/** Most recent event timestamp for a claim's trail. */
export function lastActivity(events: AuditTrailEvent[]): string {
  return events.reduce((latest, event) => (event.timestamp > latest ? event.timestamp : latest), events[0]?.timestamp ?? "");
}

/** Events newest-first, for the expanded timeline view. */
export function eventsNewestFirst(events: AuditTrailEvent[]): AuditTrailEvent[] {
  return [...events].sort((a, b) => b.timestamp.localeCompare(a.timestamp));
}

export function formatDateTime(isoTimestamp: string): string {
  const date = new Date(isoTimestamp);
  if (Number.isNaN(date.getTime())) return isoTimestamp;

  const datePart = date.toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "2-digit",
  });
  const timePart = date.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
  });

  return `${datePart} · ${timePart}`;
}
