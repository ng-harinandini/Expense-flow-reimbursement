import * as yup from "yup";

export const expenseItemSchema = yup.object({
  category: yup.string().trim().required("Expense category is required"),
  amount: yup
    .number()
    .typeError("Total amount must be a number")
    .positive("Total amount must be greater than 0")
    .required("Total amount is required"),
  currency: yup.string().trim().required("Currency is required"),
  invoiceDate: yup.string().required("Invoice date is required"),
  numberOfDays: yup
    .number()
    .typeError("Number of days must be a number")
    .min(1, "Number of days must be at least 1")
    .required("Number of days is required"),
  merchantVendor: yup.string().trim().required("Vendor name is required"),
  invoiceNumber: yup.string().trim().required("Invoice number is required"),
  travelRoute: yup.string().trim().optional(),
  travelType: yup.string().trim().optional(),
  numberOfAttendees: yup
    .number()
    .typeError("No. of attendees must be a number")
    .min(0, "No. of attendees cannot be negative")
    .optional(),
  description: yup
    .string()
    .trim()
    .required("Business purpose & description is required")
    .min(10, "Please provide at least 10 characters of context"),
});

export type ExpenseItemFormValues = yup.InferType<typeof expenseItemSchema>;

export const EXPENSE_ITEM_FORM_DEFAULTS: ExpenseItemFormValues = {
  category: "",
  amount: undefined as unknown as number,
  currency: "",
  invoiceDate: "",
  numberOfDays: 1,
  merchantVendor: "",
  invoiceNumber: "",
  travelRoute: "",
  travelType: "",
  numberOfAttendees: 1,
  description: "",
};
