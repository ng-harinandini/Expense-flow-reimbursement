"use client";

import * as React from "react";
import { useDropzone } from "react-dropzone";
import { FileCheck2, Sparkles, Upload, X } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/Button";
import { Accordion } from "@/components/ui/accordion";
import { cn } from "@/lib/utils";

import { ExtractedPolicyRuleAccordionItem } from "./ExtractedPolicyRuleAccordionItem";
import { mockExtractPolicyRulesFromFile } from "./helpers";
import type { PolicyRuleFormValues } from "./policyRuleSchema";

const ACCEPTED_TYPES = {
  "application/pdf": [".pdf"],
};
const MAX_SIZE_BYTES = 10 * 1024 * 1024;

interface ExtractedItem {
  key: string;
  values: PolicyRuleFormValues;
}

interface AddPolicyRuleWithAIDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaveRule: (values: PolicyRuleFormValues) => void;
}

export function AddPolicyRuleWithAIDialog({
  open,
  onOpenChange,
  onSaveRule,
}: AddPolicyRuleWithAIDialogProps) {
  const [file, setFile] = React.useState<File | null>(null);
  const [isExtracting, setIsExtracting] = React.useState(false);
  const [items, setItems] = React.useState<ExtractedItem[] | null>(null);

  // Reset the whole flow each time the dialog opens/closes.
  React.useEffect(() => {
    if (!open) {
      setFile(null);
      setIsExtracting(false);
      setItems(null);
    }
  }, [open]);

  const onDrop = React.useCallback((acceptedFiles: File[]) => {
    const accepted = acceptedFiles[0];
    if (!accepted) return;

    setFile(accepted);
    setItems(null);
    setIsExtracting(true);

    // TODO: replace with the real document-extraction API call.
    mockExtractPolicyRulesFromFile(accepted).then((extracted) => {
      setItems(
        extracted.map((values, index) => ({
          key: `extracted-${Date.now()}-${index}`,
          values,
        }))
      );
      setIsExtracting(false);
    });
  }, []);

  const {
    getRootProps,
    getInputProps,
    isDragActive,
    open: openFileDialog,
  } = useDropzone({
    onDrop,
    accept: ACCEPTED_TYPES,
    maxSize: MAX_SIZE_BYTES,
    maxFiles: 1,
    noClick: true,
    noKeyboard: true,
  });

  const handleItemSave = (key: string, values: PolicyRuleFormValues) => {
    onSaveRule(values);
    setItems((current) => (current ? current.filter((item) => item.key !== key) : current));
  };

  const handleItemDiscard = (key: string) => {
    setItems((current) => (current ? current.filter((item) => item.key !== key) : current));
  };

  const handleStartOver = () => {
    setFile(null);
    setItems(null);
    setIsExtracting(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader className="pr-8">
          <DialogTitle>Add policy rules with AI</DialogTitle>
          <DialogDescription>
            Upload a policy document (PDF). AI will extract the rules for you to review before
            saving.
          </DialogDescription>
        </DialogHeader>

        {!file && (
          <div
            {...getRootProps()}
            className={cn(
              "flex min-h-56 flex-col items-center justify-center gap-4 rounded-xl border-2 border-dashed p-6 text-center transition-colors",
              isDragActive ? "border-primary bg-primary/5" : "border-border bg-muted/30"
            )}
          >
            <input {...getInputProps()} />
            <div className="flex size-16 items-center justify-center rounded-full bg-primary/10">
              <Upload className="size-7 text-primary" />
            </div>
            <div>
              <p className="text-sm font-semibold text-foreground">
                Drag a policy document here
              </p>
              <p className="mt-1 text-xs text-muted-foreground">PDF, up to 10MB</p>
            </div>
            <Button type="button" variant="outline" size="sm" onClick={openFileDialog}>
              Browse files
            </Button>
          </div>
        )}

        {file && (
          <div className="space-y-4">
            <div className="flex items-center justify-between gap-3 rounded-lg border bg-card p-3">
              <div className="flex min-w-0 items-center gap-3">
                <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-primary/10">
                  <FileCheck2 className="size-4 text-primary" />
                </div>
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-foreground">{file.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {(file.size / 1024).toFixed(0)} KB
                  </p>
                </div>
              </div>
              {!isExtracting && (
                <Button type="button" variant="outline" size="sm" onClick={handleStartOver}>
                  <X />
                  Replace
                </Button>
              )}
            </div>

            {isExtracting && (
              <div className="flex items-center gap-2 rounded-lg border border-primary/20 bg-primary/5 px-3 py-2 text-xs font-medium text-primary">
                <Sparkles className="size-3.5 shrink-0 animate-pulse" />
                Extracting policy rules from the document…
              </div>
            )}

            {items && items.length > 0 && (
              <>
                <div className="flex items-center gap-2 rounded-lg border border-primary/20 bg-primary/5 px-3 py-2 text-xs font-medium text-primary">
                  <Sparkles className="size-3.5 shrink-0" />
                  Found {items.length} rule{items.length > 1 ? "s" : ""} — review each below, then
                  save.
                </div>
                <Accordion type="multiple" className="rounded-lg border px-4">
                  {items.map((item, index) => (
                    <ExtractedPolicyRuleAccordionItem
                      key={item.key}
                      itemValue={item.key}
                      index={index}
                      extractedValues={item.values}
                      onSave={(values) => handleItemSave(item.key, values)}
                      onDiscard={() => handleItemDiscard(item.key)}
                    />
                  ))}
                </Accordion>
              </>
            )}

            {items && items.length === 0 && (
              <div className="rounded-lg border bg-muted/30 p-4 text-center text-sm text-muted-foreground">
                All extracted rules have been processed.
              </div>
            )}
          </div>
        )}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
