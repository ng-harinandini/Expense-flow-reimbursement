import * as yup from "yup";

export const claimSchema = yup.object({
  merchantVendor: yup.string().trim().required("Merchant / vendor is required"),
  expenseDate: yup.string().required("Expense date is required"),
  category: yup.string().trim().required("Category is required"),
  amount: yup
    .number()
    .typeError("Amount must be a number")
    .positive("Amount must be greater than 0")
    .required("Amount is required"),
  taxAmount: yup
    .number()
    .typeError("Tax / GST must be a number")
    .min(0, "Tax / GST cannot be negative")
    .optional(),
  paymentMethod: yup.string().trim().required("Payment method is required"),
  description: yup
    .string()
    .trim()
    .required("Business purpose & description is required")
    .min(10, "Please provide at least 10 characters of context"),
  lineItems: yup
    .array(
      yup.object({
        name: yup.string().trim().required("Expense name is required"),
        amount: yup
          .number()
          .typeError("Amount must be a number")
          .positive("Amount must be greater than 0")
          .required("Amount is required"),
      })
    )
    .default([]),
});

export type ClaimFormValues = yup.InferType<typeof claimSchema>;
export type ClaimLineItem = ClaimFormValues["lineItems"][number];

export const CLAIM_FORM_DEFAULTS: ClaimFormValues = {
  merchantVendor: "",
  expenseDate: "",
  category: "",
  amount: undefined as unknown as number,
  taxAmount: undefined,
  paymentMethod: "",
  description: "",
  lineItems: [],
};
