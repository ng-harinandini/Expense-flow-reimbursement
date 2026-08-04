import { apiUpload } from "./client";

export interface ReceiptExtraction {
  // --- stored-file provenance, echoed back verbatim at submit time ---
  fileUrl: string | null;
  fileName: string;
  mimeType: string | null;
  fileSizeBytes: number | null;
  /** SHA-256 hex computed server-side; drives duplicate-receipt detection. */
  fileHash: string;

  // --- extraction ---
  ocrSource: string;
  /** Per-field confidences on a 0-100 scale, e.g. `{ vendor: 98.2, total: 99.1 }`. */
  ocrConfidence: Record<string, number> | null;
  /** Verbatim extractor output. */
  extraction: Record<string, unknown> | null;

  // --- prefill, presented as editable defaults ---
  suggestedVendor: string | null;
  suggestedDate: string | null;
  suggestedAmount: number | null;
  suggestedCurrency: string | null;
  // --- advisory, never blocking ---
  duplicateOfClaimNumber: string | null;
  errorMessage: string | null;
}

/**
 * Uploads a receipt for extraction. Returns a 200 with the extracted data —
 * no claim and no expense item is created.
 */
export function uploadReceipt(
  file: File,
  options: { signal?: AbortSignal } = {}
): Promise<ReceiptExtraction> {
  const formData = new FormData();
  formData.append("file", file);

  return apiUpload<ReceiptExtraction>("/expense-items/upload", formData, {
    signal: options.signal,
  });
}
