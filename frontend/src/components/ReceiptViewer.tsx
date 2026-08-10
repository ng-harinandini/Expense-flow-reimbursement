"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { FileWarning } from "lucide-react";

import { downloadReceipt, receiptQueryKey } from "@/api/expenseItems";
import { cn } from "@/lib/utils";


function ReceiptSkeleton({ className }: { className?: string }) {
  return (
    <div
      role="status"
      aria-label="Loading receipt"
      className={cn(
        "flex w-full items-center justify-center rounded-lg border bg-muted/30 p-4",
        className
      )}
    >
      {/* The page sheet, capped so it never outgrows the frame on short containers. */}
      <div className="flex h-full max-h-full w-full max-w-[min(100%,26rem)] animate-pulse flex-col gap-4 overflow-hidden rounded-md border bg-card p-5 shadow-sm">
        {/* Vendor block + invoice meta */}
        <div className="flex items-start justify-between gap-4">
          <div className="flex flex-col gap-2">
            <div className="h-4 w-28 rounded bg-muted-foreground/25" />
            <div className="h-2.5 w-20 rounded bg-muted-foreground/15" />
          </div>
          <div className="flex flex-col items-end gap-2">
            <div className="h-2.5 w-24 rounded bg-muted-foreground/15" />
            <div className="h-2.5 w-16 rounded bg-muted-foreground/15" />
          </div>
        </div>

        <div className="h-px w-full bg-border" />

        {/* Line items */}
        <div className="flex flex-col gap-3">
          {[...Array(5)].map((_, i) => (
            <div key={i} className="flex items-center justify-between gap-4">
              <div
                className="h-2.5 rounded bg-muted-foreground/15"
                // Staggered widths read as text rather than a uniform block.
                style={{ width: `${52 + ((i * 13) % 30)}%` }}
              />
              <div className="h-2.5 w-12 shrink-0 rounded bg-muted-foreground/15" />
            </div>
          ))}
        </div>

        <div className="h-px w-full bg-border" />

        {/* Total */}
        <div className="flex items-center justify-between gap-4">
          <div className="h-3 w-16 rounded bg-muted-foreground/25" />
          <div className="h-3 w-20 rounded bg-muted-foreground/25" />
        </div>
      </div>
    </div>
  );
}

interface ReceiptViewerProps {
  fileUrl: string | null | undefined;
  alt?: string;
  className?: string;
}

export function ReceiptViewer({ fileUrl, alt = "Receipt", className }: ReceiptViewerProps) {
  const {
    data: blob,
    isPending,
    isError,
  } = useQuery({
    queryKey: receiptQueryKey(fileUrl),
    queryFn: ({ signal }) => downloadReceipt(fileUrl as string, { signal }),
    // `enabled` gates the null case, so the non-null assertion above is safe.
    enabled: Boolean(fileUrl),
    // Receipts are immutable once uploaded — refetching them is pure waste.
    staleTime: Infinity,
    gcTime: 30 * 60 * 1000,
    retry: false,
  });

  // Object URLs are only reclaimed on explicit revoke, so each blob gets exactly one URL and
  // it's revoked when the blob changes or the component unmounts. Deriving this during render
  // instead would leak a URL on every re-render.
  const [objectUrl, setObjectUrl] = React.useState<string | null>(null);
  React.useEffect(() => {
    if (!blob) {
      setObjectUrl(null);
      return;
    }
    const url = URL.createObjectURL(blob);
    setObjectUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [blob]);

  const frameClass = cn(
    "flex w-full items-center justify-center rounded-lg border bg-muted/30",
    className
  );

  if (!fileUrl) {
    return (
      <div className={cn(frameClass, "min-h-32 text-sm text-muted-foreground")}>
        No receipt attached
      </div>
    );
  }

  if (isPending || (!objectUrl && !isError)) {
    return <ReceiptSkeleton className={className} />;
  }

  if (isError || !objectUrl) {
    return (
      <div className={cn(frameClass, "min-h-32 gap-2 text-sm text-destructive")}>
        <FileWarning className="size-4" />
        Couldn&apos;t load this receipt
      </div>
    );
  }


  const isPdf = blob?.type === "application/pdf";

  if (isPdf) {
    // `#toolbar=0` hides the built-in viewer chrome (download/print/save-to-Drive, and the zoom
    // and page controls along with them) — it's a Chromium-only flag, so Firefox and Safari still
    // render the full toolbar. `view=FitH` asks the viewer to fit the page to the frame width,
    // which is what keeps the page from scrolling sideways where it's honoured.
    return (
      <iframe
        src={`${objectUrl}#toolbar=0&navpanes=0&view=FitH`}
        title={alt}
        className={cn("w-full rounded-lg border bg-muted/30", className)}
      />
    );
  }

  return (
    // eslint-disable-next-line @next/next/no-img-element -- blob: URLs can't go through next/image
    <img
      src={objectUrl}
      alt={alt}
      className={cn("w-full rounded-lg border object-contain", className)}
    />
  );
}
