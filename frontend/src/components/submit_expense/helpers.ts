import {
  BedDouble,
  Car,
  GraduationCap,
  HeartPulse,
  Laptop,
  MoreHorizontal,
  PartyPopper,
  Plane,
  Users,
  Utensils,
  Wifi,
  type LucideIcon,
} from "lucide-react";

import type { ReceiptExtraction } from "@/api/expenseItems";

import type { ExpenseItemFormValues } from "./expenseItemSchema";

export interface ReceiptDropzoneProps {
  file: File | null;
  onFileAccepted: (file: File) => void;
  onFileRemoved: () => void;
  isScanning?: boolean;
  className?: string;
}

export const ACCEPTED_TYPES = {
  "image/jpeg": [".jpg", ".jpeg"],
  "image/png": [".png"],
  "application/pdf": [".pdf"],
};

export const MAX_SIZE_BYTES = 10 * 1024 * 1024;

export const EXPENSE_CATEGORIES = [
  "Meals",
  "Ground Transport",
  "Flights",
  "Lodging",
  "Client Entertainment",
  "Communications & Connectivity",
  "Training & Professional Dev",
  "Software & Subscriptions",
  "Team Events",
  "Health & Wellness",
  "Misc / Other",
];

export const MAX_EXPENSE_ITEMS = 10;

export const CATEGORY_ICONS: Record<string, LucideIcon> = {
  Meals: Utensils,
  "Ground Transport": Car,
  Flights: Plane,
  Lodging: BedDouble,
  "Client Entertainment": PartyPopper,
  "Communications & Connectivity": Wifi,
  "Training & Professional Dev": GraduationCap,
  "Software & Subscriptions": Laptop,
  "Team Events": Users,
  "Health & Wellness": HeartPulse,
  "Misc / Other": MoreHorizontal,
};

/** Pill background + text color per category, used for the category badge in the items table. */
export const CATEGORY_PILL_COLORS: Record<string, string> = {
  Meals: "bg-amber-500/15 text-amber-600",
  "Ground Transport": "bg-emerald-500/15 text-emerald-600",
  Flights: "bg-blue-500/15 text-blue-600",
  Lodging: "bg-violet-500/15 text-violet-600",
  "Client Entertainment": "bg-pink-500/15 text-pink-600",
  "Communications & Connectivity": "bg-indigo-500/15 text-indigo-600",
  "Training & Professional Dev": "bg-teal-500/15 text-teal-600",
  "Software & Subscriptions": "bg-fuchsia-500/15 text-fuchsia-600",
  "Team Events": "bg-rose-500/15 text-rose-600",
  "Health & Wellness": "bg-cyan-500/15 text-cyan-600",
  "Misc / Other": "bg-muted text-muted-foreground",
};

/** A confirmed expense line item, ready to be grouped under the claim. */
export interface ExpenseItemDraft extends ExpenseItemFormValues {
  id: string;
  /** Kept so re-opening the item for edit doesn't require re-uploading the receipt. */
  receiptFile: File;
  receiptFileName: string;
  /** Data URL for the table thumbnail; null for file types with no visual preview (e.g. PDF). */
  receiptPreviewUrl: string | null;
  isPdf: boolean;
  extraction: ReceiptExtraction | null;
}

export function generateItemId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `item-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export function formatUsd(amount: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(amount);
}
