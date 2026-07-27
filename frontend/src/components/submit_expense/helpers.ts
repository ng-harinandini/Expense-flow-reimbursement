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
