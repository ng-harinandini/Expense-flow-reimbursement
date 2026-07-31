"use client";

import * as React from "react";
import { ChevronDown, ChevronRight, ScrollText, Search } from "lucide-react";

import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { INITIAL_AUDIT_TRAIL } from "@/data/auditLogs";

import {
  AuditStatusBadge,
  actorsInvolved,
  eventsNewestFirst,
  formatDateTime,
  lastActivity,
} from "./helpers";

const ALL_EVENTS = "all-events";
const EVENT_TYPE_OPTIONS = [
  "Claim Submitted",
  "Receipt Scanned",
  "Risk Score Computed",
  "Fraud Screening",
  "Manager Review",
  "Finance Review",
];

const ALL_ACTORS = "all-actors";

const DATE_RANGE_OPTIONS = [
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "90d", label: "Last 90 days" },
  { value: "all", label: "All time" },
];

function AuditLogs() {
  const [search, setSearch] = React.useState("");
  const [expanded, setExpanded] = React.useState<Set<string>>(new Set());

  const toggleExpanded = React.useCallback((id: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const actorOptions = React.useMemo(() => {
    const names = INITIAL_AUDIT_TRAIL.flatMap((claim) => actorsInvolved(claim.events));
    return Array.from(new Set(names)).sort((a, b) => a.localeCompare(b));
  }, []);

  const rows = React.useMemo(() => {
    const query = search.trim().toLowerCase();

    return INITIAL_AUDIT_TRAIL.filter((claim) => {
      if (!query) return true;

      const haystack = [
        claim.claimRef,
        ...actorsInvolved(claim.events),
        ...claim.events.flatMap((event) => [event.label, event.detail]),
      ]
        .join(" ")
        .toLowerCase();

      return haystack.includes(query);
    });
  }, [search]);

  return (
    <div className="rounded-xl border bg-card shadow-sm">
      <div className="flex flex-col gap-4 p-6 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-4">
          <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-primary/10">
            <ScrollText className="size-6 text-primary" />
          </div>
          <div className="min-w-0">
            <h2 className="text-xl font-semibold text-foreground">Audit Logs</h2>
            <p className="text-sm text-muted-foreground">
              Full audit trail of every claim event across employees, approvers, and the AI engine
            </p>
          </div>
        </div>
      </div>

      <div className="flex flex-col gap-3 px-6 pb-4 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by actor, entity, event..."
            className="pl-9 text-foreground"
          />
        </div>

        <Select defaultValue={ALL_EVENTS}>
          <SelectTrigger className="h-9 text-foreground sm:w-44">
            <SelectValue placeholder="All events" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_EVENTS}>All events</SelectItem>
            {EVENT_TYPE_OPTIONS.map((option) => (
              <SelectItem key={option} value={option}>
                {option}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select defaultValue={ALL_ACTORS}>
          <SelectTrigger className="h-9 text-foreground sm:w-44">
            <SelectValue placeholder="All employees" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_ACTORS}>All employees</SelectItem>
            {actorOptions.map((option) => (
              <SelectItem key={option} value={option}>
                {option}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select defaultValue="30d">
          <SelectTrigger className="h-9 text-foreground sm:w-44">
            <SelectValue placeholder="Last 30 days" />
          </SelectTrigger>
          <SelectContent>
            {DATE_RANGE_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="overflow-x-auto px-6 pb-6">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <th className="w-10 py-3" />
              <th className="py-3 pr-4">Claim Ref</th>
              <th className="py-3 pr-4">Employees Involved</th>
              <th className="py-3 pr-4">Events</th>
              <th className="py-3 pr-4">Last Activity</th>
              <th className="py-3 pr-4">Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={6} className="py-10 text-center text-sm text-muted-foreground">
                  No audit records found matching your filter criteria.
                </td>
              </tr>
            )}
            {rows.map((claim) => {
              const isExpanded = expanded.has(claim.id);

              return (
                <React.Fragment key={claim.id}>
                  <tr
                    className="cursor-pointer border-b last:border-b-0 hover:bg-accent/40"
                    onClick={() => toggleExpanded(claim.id)}
                  >
                    <td className="py-3.5 pl-1 text-muted-foreground">
                      {isExpanded ? (
                        <ChevronDown className="size-4" />
                      ) : (
                        <ChevronRight className="size-4" />
                      )}
                    </td>
                    <td className="py-3.5 pr-4 font-semibold text-foreground">{claim.claimRef}</td>
                    <td className="py-3.5 pr-4 text-secondary">
                      {actorsInvolved(claim.events).join(", ")}
                    </td>
                    <td className="py-3.5 pr-4 text-foreground">{claim.events.length}</td>
                    <td className="py-3.5 pr-4 whitespace-nowrap text-muted-foreground">
                      {formatDateTime(lastActivity(claim.events))}
                    </td>
                    <td className="py-3.5 pr-4">
                      <AuditStatusBadge claim={claim} />
                    </td>
                  </tr>
                  {isExpanded && (
                    <tr className="border-b bg-muted/30 last:border-b-0">
                      <td colSpan={6} className="px-1 py-2">
                        <div className="divide-y divide-border/60 pl-9">
                          {eventsNewestFirst(claim.events).map((event) => (
                            <div
                              key={event.id}
                              className="flex flex-col gap-1 py-2 text-sm sm:flex-row sm:items-center sm:gap-4"
                            >
                              <span className="w-44 shrink-0 font-mono text-xs text-muted-foreground">
                                {formatDateTime(event.timestamp)}
                              </span>
                              <span
                                className={cn(
                                  "w-28 shrink-0 font-semibold",
                                  event.actorType === "ai" ? "text-secondary" : "text-foreground"
                                )}
                              >
                                {event.actor}
                              </span>
                              <span className="text-muted-foreground">
                                {event.label} <span className="mx-1">→</span> {event.detail}
                              </span>
                            </div>
                          ))}
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default AuditLogs;
