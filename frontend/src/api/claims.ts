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
    merchantVendor: draft.merchantVendor || undefined,
    expenseDate: draft.expenseFromDate || undefined,
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
  status?: string;
  employeeId?: string;
  riskLevel?: string;
  limit?: number;
  offset?: number;
}

export const CLAIMS_QUERY_KEY = ["claims"] as const;

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
  }>;
  workflowHistory: WorkflowStepLog[];
  withdrawnAt: string | null;
  withdrawalReason: string | null;
  [key: string]: unknown;
}

function mapClaim(raw: ClaimApiShape): Claim {
  const items: ClaimExpenseItem[] = (raw.items ?? []).map((i) => ({
    id: i.id,
    category: (i.category ?? "Misc / Other") as ClaimExpenseItem["category"],
    merchantVendor: i.merchantVendor ?? "",
    expenseDate: i.expenseDate ?? "",
    description: i.purposeDescription ?? "",
    amount: i.amount ?? 0,
    currency: i.currency ?? "USD",
    receiptUrl: i.fileUrl ?? "",
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

export function getClaims(params: GetClaimsParams = {}): Promise<Claim[]> {
  const query = new URLSearchParams();
  if (params.status) query.set("status", params.status);
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
