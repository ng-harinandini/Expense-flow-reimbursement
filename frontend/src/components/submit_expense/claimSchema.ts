import * as yup from "yup";

export const claimSchema = yup.object({
  claimName: yup.string().trim().required("Claim title is required"),
  fromDate: yup.string().required("From date is required"),
  toDate: yup
    .string()
    .required("To date is required")
    .test(
      "to-not-before-from",
      "To date can't be before the from date",
      function toNotBeforeFrom(value) {
        const { fromDate } = this.parent as { fromDate?: string };
        if (!value || !fromDate) return true;
        return new Date(value) >= new Date(fromDate);
      }
    ),
  purpose: yup
    .string()
    .trim()
    .max(300, "Claim purpose must be 300 characters or less")
    .default(""),
});

export type ClaimFormValues = yup.InferType<typeof claimSchema>;

export const CLAIM_FORM_DEFAULTS: ClaimFormValues = {
  claimName: "",
  fromDate: "",
  toDate: "",
  purpose: "",
};
