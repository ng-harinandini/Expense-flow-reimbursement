import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { STATUS_BADGE_CLASSES, STATUS_LABELS } from "../my_claims/status";
import type { AuditTrailClaim, AuditTrailEvent, ClaimStatus } from "@/types";

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
