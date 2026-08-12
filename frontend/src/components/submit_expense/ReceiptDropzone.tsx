"use client";

import * as React from "react";
import { useDropzone } from "react-dropzone";
import { Download, ExternalLink, FileCheck2, Sparkles, Upload } from "lucide-react";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/Button";
import {
  ACCEPTED_TYPES,
  MAX_SIZE_BYTES,
  type ReceiptDropzoneProps,
} from "@/components/submit_expense/helpers";

export function ReceiptDropzone({
  file,
  onFileAccepted,
  onFileRemoved,
  isScanning = false,
  className,
}: ReceiptDropzoneProps) {
  const [previewUrl, setPreviewUrl] = React.useState<string | null>(null);
  const [blobUrl, setBlobUrl] = React.useState<string | null>(null);
  const isPdf = file?.type === "application/pdf";

  const onDrop = React.useCallback(
    (acceptedFiles: File[]) => {
      const accepted = acceptedFiles[0];
      if (accepted) onFileAccepted(accepted);
    },
    [onFileAccepted]
  );

  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({
    onDrop,
    accept: ACCEPTED_TYPES,
    maxSize: MAX_SIZE_BYTES,
    maxFiles: 1,
    noClick: true,
    noKeyboard: true,
  });

  React.useEffect(() => {
    if (!file) {
      setPreviewUrl(null);
      setBlobUrl(null);
      return;
    }

    let cancelled = false;
    const reader = new FileReader();
    reader.onload = () => {
      if (!cancelled && typeof reader.result === "string") {
        setPreviewUrl(reader.result);
      }
    };
    reader.readAsDataURL(file);

    // Blob URL is used for opening/downloading in a new tab; unlike a data:
    // URI, browsers navigate to it cleanly instead of showing the raw string.
    const url = URL.createObjectURL(file);
    setBlobUrl(url);

    return () => {
      cancelled = true;
      URL.revokeObjectURL(url);
    };
  }, [file]);

  if (file) {
    return (
      <div className={cn("w-full rounded-xl border bg-card text-left shadow-sm", className)}>
        <div className="relative overflow-hidden rounded-t-xl bg-background p-3">
          {previewUrl && !isPdf ? (
            <div className="relative min-h-40 overflow-hidden rounded-lg border">
              <img
                src={previewUrl}
                alt={file.name}
                className="max-h-64 w-full object-contain"
              />
              {isScanning && (
                <>
                  <div className="pointer-events-none absolute inset-0 bg-primary/5" />
                  <div key={file.name} className="scan-sweep-bar pointer-events-none" />
                </>
              )}
            </div>
          ) : (
            <div className="relative flex min-h-40 items-center justify-center overflow-hidden rounded-lg border">
              <div className="flex size-16 items-center justify-center rounded-full bg-primary/10">
                <FileCheck2 className="size-8 text-primary" />
              </div>
              {isScanning && (
                <>
                  <div className="pointer-events-none absolute inset-0 bg-primary/5" />
                  <div key={file.name} className="scan-sweep-bar pointer-events-none" />
                </>
              )}
            </div>
          )}

          {isScanning && (
            <Badge className="absolute top-6 right-6 gap-1.5 bg-background/90 text-foreground shadow-sm">
              <Sparkles className="size-3 animate-pulse text-primary" />
              Scanning…
            </Badge>
          )}

          {previewUrl && !isScanning && (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="absolute right-6 bottom-6 shadow-sm"
              onClick={(e) => {
                e.stopPropagation();
                if (blobUrl) window.open(blobUrl, "_blank", "noopener,noreferrer");
              }}
            >
              Open
              <ExternalLink />
            </Button>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 border-t p-3">
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-foreground">{file.name}</p>
            <p className="text-xs text-muted-foreground">
              {(file.size / 1024).toFixed(0)} KB
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {previewUrl && (
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label="Save file"
                onClick={(e) => {
                  e.stopPropagation();
                  if (!blobUrl) return;
                  const a = document.createElement("a");
                  a.href = blobUrl;
                  a.download = file.name;
                  a.click();
                }}
              >
                <Download />
              </Button>
            )}
            {/* Deliberately still available mid-scan: the scan now includes a model call and can
                run for a while, and onFileRemoved aborts the in-flight upload. Hiding this was the
                only escape hatch, leaving a reload as the alternative. */}
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => onFileRemoved()}
            >
              {isScanning ? "Cancel" : "Remove"}
            </Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      {...getRootProps()}
      className={cn(
        "flex min-h-80 flex-col items-center justify-center gap-4 rounded-xl border-2 border-dashed p-6 text-center transition-colors",
        isDragActive ? "border-primary bg-primary/5" : "border-border bg-muted/30",
        className
      )}
    >
      <input {...getInputProps()} />
      <div className="flex size-16 items-center justify-center rounded-full bg-primary/10">
        <Upload className="size-7 text-primary" />
      </div>
      <div>
        <p className="text-sm font-semibold text-foreground">Drag a receipt here</p>
        <p className="mt-1 text-xs text-muted-foreground">
          JPG, PNG or PDF, up to 10MB
        </p>
      </div>
      <Button type="button" variant="outline" size="sm" onClick={open}>
        Browse files
      </Button>
    </div>
  );
}
