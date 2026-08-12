import * as yup from "yup";

export const expenseItemSchema = yup.object({
  category: yup.string().trim().required("Expense category is required"),
  amount: yup
    .number()
    .typeError("Total amount must be a number")
    .positive("Total amount must be greater than 0")
    .required("Total amount is required"),
  currency: yup.string().trim().required("Currency is required"),
  taxAmount: yup
    .number()
    .typeError("Tax / GST must be a number")
    .min(0, "Tax / GST cannot be negative")
    .optional(),
  expenseFromDate: yup.string().required("Invoice from date is required"),
  expenseToDate: yup
    .string()
    .required("Invoice to date is required")
    .test(
      "to-not-before-from",
      "To date can't be before the from date",
      function toNotBeforeFrom(value) {
        const { expenseFromDate } = this.parent as { expenseFromDate?: string };
        if (!value || !expenseFromDate) return true;
        return new Date(value) >= new Date(expenseFromDate);
      }
    ),
  merchantVendor: yup.string().trim().required("Vendor name is required"),
  invoiceNumber: yup.string().trim().required("Invoice number is required"),
  // This app is primarily used for travel expenses, so these are required rather than optional.
  travelRoute: yup.string().trim().required("Travel route is required"),
  travelType: yup.string().trim().required("Travel type is required"),
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
  taxAmount: undefined,
  expenseFromDate: "",
  expenseToDate: "",
  merchantVendor: "",
  invoiceNumber: "",
  travelRoute: "",
  travelType: "",
  numberOfAttendees: undefined,
  description: "",
};
