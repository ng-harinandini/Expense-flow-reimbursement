import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "./client";
import type { ReceiptExtraction } from "./expenseItems";
import type { ClaimFormValues } from "@/components/submit_expense/claimSchema";
import type { ExpenseItemDraft } from "@/components/submit_expense/helpers";
import type { Claim, ClaimExpenseItem, ClaimStatus, WorkflowStepLog } from "@/types";

interface CreateClaimItem {
  category?: string;
  amount?: number;
  currency?: string;
  merchantVendor?: string;
  expenseDate?: string;
  purposeDescription?: string;
  receiptAttached?: boolean;
  fileUrl?: string | null;
  fileName?: string | null;
  mimeType?: string | null;
  fileSizeBytes?: number | null;
  fileHash?: string;
  ocrSource?: string;
  ocrConfidence?: Record<string, number> | null;
  ocrExtractedJson?: Record<string, unknown> | null;
  /**
   * Scope for the local-travel policy engine (see doc/travel-policy-rules.md) — must be sent at
   * this top level, not inside `employeeCorrectedData`, or `evaluate_travel_policy` never matches
   * a rule for the item regardless of what the employee selected.
   */
  travelType?: string;
  duration?: string;
  /**
   * Fields the form collects that have no dedicated column on `ExpenseItemCreateSchema`
   * (invoice number, travel route, attendee/day counts). Packed here rather than dropped
   * silently — the backend already accepts this as freeform JSON.
   */
  employeeCorrectedData?: Record<string, unknown> | null;
}

interface CreateClaimRequest {
  title?: string;
  purpose?: string;
  fromDate?: string;
  toDate?: string;
  currency: string;
  items: CreateClaimItem[];
}

export interface CreateClaimResponse {
  id: string;
  claimNumber: string;
  status: string;
  [key: string]: unknown;
}

function buildItem(draft: ExpenseItemDraft): CreateClaimItem {
  const ext: ReceiptExtraction | null = draft.extraction;
  return {
    category: draft.category || undefined,
    amount: draft.amount,
    currency: draft.currency || undefined,
    merchantVendor: draft.merchantVendor || undefined,
    expenseDate: draft.invoiceDate || undefined,
    purposeDescription: draft.description || undefined,
    receiptAttached: true,
    fileUrl: ext?.fileUrl ?? null,
    fileName: ext?.fileName ?? draft.receiptFileName,
    mimeType: ext?.mimeType ?? null,
    fileSizeBytes: ext?.fileSizeBytes ?? null,
    fileHash: ext?.fileHash,
    ocrSource: ext?.ocrSource,
    ocrConfidence: ext?.ocrConfidence ?? null,
    ocrExtractedJson: (ext?.extraction as Record<string, unknown> | null) ?? null,
    // The only two values the seeded rules currently scope on (doc/travel-policy-rules.md) are
    // "Local" travel taken over a "Day" — there's no UI control for `duration` yet, and every
    // rule's duration axis is either "Day" or a wildcard, so this is never wrong to send.
    travelType: draft.travelType || undefined,
    duration: "Day",
    employeeCorrectedData: {
      invoiceNumber: draft.invoiceNumber || undefined,
      travelRoute: draft.travelRoute || undefined,
      numberOfAttendees: draft.numberOfAttendees,
      numberOfDays: draft.numberOfDays,
    },
  };
}

export function createClaim(
  claim: ClaimFormValues,
  items: ExpenseItemDraft[]
): Promise<CreateClaimResponse> {
  const body: CreateClaimRequest = {
    title: claim.claimName || undefined,
    purpose: claim.purpose || undefined,
    fromDate: claim.fromDate || undefined,
    toDate: claim.toDate || undefined,
    currency: "USD",
    items: items.map(buildItem),
  };

  return apiRequest<CreateClaimResponse>("/claims", {
    method: "POST",
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json" },
  });
}

// ---------------------------------------------------------------------------
// GET /claims
// ---------------------------------------------------------------------------

export interface GetClaimsParams {
  /** One status, or several (OR'd) — sent as repeated `?status=` params in a single request. */
  status?: string | string[];
  employeeId?: string;
  riskLevel?: string;
  limit?: number;
  offset?: number;
}

export const CLAIMS_QUERY_KEY = ["claims"] as const;

/**
 * Statuses the AI pipeline can still be working through. A claim in one of these is polled by
 * {@link useClaimsQuery}/{@link useClaimQuery}; anything else (a terminal outcome, or `Failed`)
 * stops polling.
 */
const IN_PROGRESS_STATUSES: ReadonlySet<ClaimStatus> = new Set([
  "Submitted",
  "Processing",
]);

/** Raw shape the backend serialises — `title` maps to the frontend's `claimTitle`. */
interface ClaimApiShape {
  id: string;
  claimNumber: string;
  employeeId: string | null;
  employeeName: string | null;
  title: string | null;
  fromDate: string | null;
  toDate: string | null;
  status: string;
  totalAmount: number | null;
  items: Array<{
    id: string;
    category: string | null;
    merchantVendor: string | null;
    expenseDate: string | null;
    purposeDescription: string | null;
    amount: number | null;
    currency: string | null;
    fileUrl: string | null;
    status: string | null;
  }>;
  workflowHistory: WorkflowStepLog[];
  withdrawnAt: string | null;
  withdrawalReason: string | null;
  holdReason?: string | null;
  [key: string]: unknown;
}

function mapClaim(raw: ClaimApiShape): Claim {
  const items: ClaimExpenseItem[] = (raw.items ?? []).map((i) => ({
    id: i.id,
    category: (i.category ?? "Miscellaneous / Others") as ClaimExpenseItem["category"],
    merchantVendor: i.merchantVendor ?? "",
    expenseDate: i.expenseDate ?? "",
    description: i.purposeDescription ?? "",
    amount: i.amount ?? 0,
    currency: i.currency ?? "USD",
    receiptUrl: i.fileUrl ?? "",
    status: (i.status ?? "Submitted") as ClaimExpenseItem["status"],
  }));

  return {
    id: raw.id,
    claimNumber: raw.claimNumber,
    employeeId: raw.employeeId ?? "",
    employeeName: raw.employeeName ?? "",
    claimTitle: raw.title ?? "",
    fromDate: raw.fromDate ?? "",
    toDate: raw.toDate ?? "",
    status: raw.status as ClaimStatus,
    // The server maintains this roll-up; summing items is only a fallback for older payloads.
    totalAmount:
      raw.totalAmount ?? items.reduce((sum, item) => sum + item.amount, 0),
    items,
    workflowHistory: raw.workflowHistory ?? [],
    withdrawnAt: raw.withdrawnAt ?? null,
    withdrawalReason: raw.withdrawalReason ?? null,
    holdReason: raw.holdReason ?? null,
  };
}

// ---------------------------------------------------------------------------
// POST /claims/{id}/withdraw
// ---------------------------------------------------------------------------

export interface WithdrawClaimOptions {
  reason: string;
  expectedVersion?: number;
}

/**
 * Withdraw an entire claim. Employees may only withdraw their own, and only before it is
 * approved — the backend answers 409 once it is Approved/Disbursed and 403 while it is under
 * fraud investigation. Resolves to the updated claim.
 */
export function withdrawClaim(
  claimId: string,
  options: WithdrawClaimOptions = { reason: "Withdrawn by employee" },
): Promise<Claim> {
  const body: Record<string, unknown> = {};
  if (options.reason) body.reason = options.reason;
  if (options.expectedVersion != null) body.expectedVersion = options.expectedVersion;

  return apiRequest<ClaimApiShape>(`/claims/${encodeURIComponent(claimId)}/withdraw`, {
    method: "POST",
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json" },
  }).then(mapClaim);
}

/** Appends one `?status=` param per value — the backend OR's repeated params into one query. */
function appendStatus(query: URLSearchParams, status: GetClaimsParams["status"]): void {
  if (!status) return;
  for (const value of Array.isArray(status) ? status : [status]) {
    if (value) query.append("status", value);
  }
}

export function getClaims(params: GetClaimsParams = {}): Promise<Claim[]> {
  const query = new URLSearchParams();
  appendStatus(query, params.status);
  if (params.employeeId) query.set("employeeId", params.employeeId);
  if (params.riskLevel) query.set("riskLevel", params.riskLevel);
  if (params.limit != null) query.set("limit", String(params.limit));
  if (params.offset != null) query.set("offset", String(params.offset));

  const path = query.toString() ? `/claims?${query}` : "/claims";

  return apiRequest<ClaimApiShape[]>(path).then((list) => list.map(mapClaim));
}

export function useClaimsQuery(params: GetClaimsParams = {}) {
  return useQuery({
    queryKey: [...CLAIMS_QUERY_KEY, params],
    queryFn: () => getClaims(params),
    // Keep polling the list while any row is still being processed by the background pipeline, so
    // a just-submitted claim's status visibly updates (Submitted -> Auto_Approved/.../Failed)
    // without the user having to reload the page.
    refetchInterval: (query) => {
      const claims = query.state.data;
      const hasInProgress = claims?.some((c) => IN_PROGRESS_STATUSES.has(c.status));
      return hasInProgress ? 3000 : false;
    },
  });
}

// ---------------------------------------------------------------------------
// GET /claims/{id}
// ---------------------------------------------------------------------------

export function getClaim(claimId: string): Promise<Claim> {
  return apiRequest<ClaimApiShape>(`/claims/${encodeURIComponent(claimId)}`).then(mapClaim);
}

/**
 * One claim, polled every few seconds while it's still `Submitted`/`Processing` — the window
 * during which `POST /claims` has responded but the background pipeline (policy, fraud,
 * classification) hasn't routed it yet. Polling stops itself the moment the claim reaches a
 * terminal status or `Failed`, so an approved/rejected/held claim being viewed doesn't keep
 * refetching forever.
 */
export function useClaimQuery(claimId: string | undefined, options: { enabled?: boolean } = {}) {
  const queryClient = useQueryClient();
  const wasInProgress = useRef(false);

  const query = useQuery({
    queryKey: [...CLAIMS_QUERY_KEY, claimId],
    queryFn: () => getClaim(claimId as string),
    enabled: Boolean(claimId) && (options.enabled ?? true),
    refetchInterval: (q) => {
      const status = q.state.data?.status;
      return status && IN_PROGRESS_STATUSES.has(status) ? 3000 : false;
    },
  });

  const status = query.data?.status;
  useEffect(() => {
    if (!status) return;
    const inProgress = IN_PROGRESS_STATUSES.has(status);
    // The claims list (My Claims grid) has its own cached snapshot from whenever it last loaded —
    // refresh it once this claim leaves Submitted/Processing so the grid's row updates too,
    // without the user having to manually reload the page.
    if (wasInProgress.current && !inProgress) {
      queryClient.invalidateQueries({ queryKey: CLAIMS_QUERY_KEY });
    }
    wasInProgress.current = inProgress;
  }, [status, queryClient]);

  return query;
}

// ---------------------------------------------------------------------------
// GET /claims/team
// ---------------------------------------------------------------------------

export const TEAM_CLAIMS_QUERY_KEY = ["claims", "team"] as const;

/**
 * Claims filed by the caller's direct reports (manager-only). Same query params and response
 * shape as {@link getClaims} — just scoped server-side to the manager's own team.
 */
export function getTeamClaims(params: GetClaimsParams = {}): Promise<Claim[]> {
  const query = new URLSearchParams();
  appendStatus(query, params.status);
  if (params.riskLevel) query.set("riskLevel", params.riskLevel);
  if (params.limit != null) query.set("limit", String(params.limit));
  if (params.offset != null) query.set("offset", String(params.offset));

  const path = query.toString() ? `/claims/team?${query}` : "/claims/team";

  return apiRequest<ClaimApiShape[]>(path).then((list) => list.map(mapClaim));
}

export function useTeamClaimsQuery(params: GetClaimsParams = {}) {
  return useQuery({
    queryKey: [...TEAM_CLAIMS_QUERY_KEY, params],
    queryFn: () => getTeamClaims(params),
  });
}

// ---------------------------------------------------------------------------
// POST /claims/{id}/action
// ---------------------------------------------------------------------------

// "DISBURSE" was retired — Approve is the final reviewer step (see
// app.domain.claim_state_machine on the backend, which no longer accepts that action at all).
export type ClaimReviewAction = "APPROVE" | "REJECT" | "FLAG_FRAUD";

export interface ExecuteClaimActionOptions {
  claimId: string;
  action: ClaimReviewAction;
  notes?: string;
  expectedVersion?: number;
}

/**
 * Apply a reviewer decision. The backend derives the target state from the claim's current
 * status (e.g. APPROVE escalates Manager_Review -> Finance_Review) and 409s with the legal next
 * states if the action isn't valid from where the claim currently is.
 */
export function executeClaimAction({
  claimId,
  action,
  notes,
  expectedVersion,
}: ExecuteClaimActionOptions): Promise<Claim> {
  const body: Record<string, unknown> = { action };
  if (notes) body.notes = notes;
  if (expectedVersion != null) body.expectedVersion = expectedVersion;

  return apiRequest<ClaimApiShape>(`/claims/${encodeURIComponent(claimId)}/action`, {
    method: "POST",
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json" },
  }).then(mapClaim);
}

export function useExecuteClaimActionMutation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: executeClaimAction,
    onSuccess: () => {
      // The acted-on claim can appear in both the caller's own list and their team list
      // (e.g. an admin), so both are invalidated rather than guessing which one is stale.
      queryClient.invalidateQueries({ queryKey: CLAIMS_QUERY_KEY });
      queryClient.invalidateQueries({ queryKey: TEAM_CLAIMS_QUERY_KEY });
    },
  });
}

export function useWithdrawClaimMutation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      claimId,
      reason,
      expectedVersion,
    }: {
      claimId: string;
      reason: string;
      expectedVersion?: number;
    }) => withdrawClaim(claimId, { reason, expectedVersion }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: CLAIMS_QUERY_KEY });
    },
  });
}
