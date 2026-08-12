import {
  BedDouble,
  Briefcase,
  Car,
  Fuel,
  GraduationCap,
  Laptop,
  MoreHorizontal,
  ParkingCircle,
  PartyPopper,
  Phone,
  Plane,
  Send,
  TrainFront,
  Utensils,
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
  "Air Travel",
  "Train Travel",
  "Taxi / Cab / Ride-hailing",
  "Rental Car",
  "Fuel / Mileage",
  "Hotel / Lodging",
  "Meals",
  "Client / Business Entertainment",
  "Parking & Tolls",
  "Communication",
  "Training / Certification / Conference",
  "Office Supplies / Equipment",
  "Software / Subscriptions",
  "Courier / Postage",
  "Miscellaneous / Others",
];

export const CURRENCY_CODES = [
  "INR",
  "USD",
  "EUR",
  "GBP",
  "AUD",
  "BRL",
  "CAD",
  "CHF",
  "CNY",
  "CZK",
  "DKK",
  "HKD",
  "HUF",
  "IDR",
  "ILS",
  "ISK",
  "JPY",
  "KRW",
  "MXN",
  "MYR",
  "NOK",
  "NZD",
  "PHP",
  "PLN",
  "RON",
  "SEK",
  "SGD",
  "THB",
  "TRY",
  "ZAR",
];

export const MAX_EXPENSE_ITEMS = 10;

export const CATEGORY_ICONS: Record<string, LucideIcon> = {
  "Air Travel": Plane,
  "Train Travel": TrainFront,
  "Taxi / Cab / Ride-hailing": Car,
  "Rental Car": Car,
  "Fuel / Mileage": Fuel,
  "Hotel / Lodging": BedDouble,
  Meals: Utensils,
  "Client / Business Entertainment": PartyPopper,
  "Parking & Tolls": ParkingCircle,
  Communication: Phone,
  "Training / Certification / Conference": GraduationCap,
  "Office Supplies / Equipment": Briefcase,
  "Software / Subscriptions": Laptop,
  "Courier / Postage": Send,
  "Miscellaneous / Others": MoreHorizontal,
};

/** Pill background + text color per category, used for the category badge in the items table. */
export const CATEGORY_PILL_COLORS: Record<string, string> = {
  "Air Travel": "bg-blue-500/15 text-blue-600",
  "Train Travel": "bg-sky-500/15 text-sky-600",
  "Taxi / Cab / Ride-hailing": "bg-emerald-500/15 text-emerald-600",
  "Rental Car": "bg-lime-500/15 text-lime-600",
  "Fuel / Mileage": "bg-orange-500/15 text-orange-600",
  "Hotel / Lodging": "bg-violet-500/15 text-violet-600",
  Meals: "bg-amber-500/15 text-amber-600",
  "Client / Business Entertainment": "bg-pink-500/15 text-pink-600",
  "Parking & Tolls": "bg-slate-500/15 text-slate-600",
  Communication: "bg-indigo-500/15 text-indigo-600",
  "Training / Certification / Conference": "bg-teal-500/15 text-teal-600",
  "Office Supplies / Equipment": "bg-yellow-500/15 text-yellow-700",
  "Software / Subscriptions": "bg-fuchsia-500/15 text-fuchsia-600",
  "Courier / Postage": "bg-cyan-500/15 text-cyan-600",
  "Miscellaneous / Others": "bg-muted text-muted-foreground",
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

/** `YYYY-MM-DD` (the date input's value format) as `dd/mm/yyyy`.
*/
export function formatDdMmYyyy(isoDate: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate);
  if (!match) return isoDate;
  const [, year, month, day] = match;
  return `${day}/${month}/${year}`;
}
