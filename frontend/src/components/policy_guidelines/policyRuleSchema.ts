import * as yup from "yup";

export const policyRuleSchema = yup.object({
  category: yup.string().trim().required("Category is required"),
  gradeApplicable: yup.string().trim().required("Grade applicable is required"),
  maxAmount: yup
    .number()
    .typeError("Max amount must be a number")
    .positive("Max amount must be greater than 0")
    .required("Max amount is required"),
  maxAmountUnit: yup.string().trim().required("Unit is required"),
  autoApproveLimit: yup
    .number()
    .typeError("Auto approve limit must be a number")
    .min(0, "Auto approve limit cannot be negative")
    .optional()
    .transform((value, original) => (original === "" ? undefined : value)),
  requiresReceiptAbove: yup
    .number()
    .typeError("Requires receipt above must be a number")
    .min(0, "Requires receipt above cannot be negative")
    .required("Requires receipt above is required"),
  effectiveFrom: yup.string().required("Effective from date is required"),
  description: yup.string().trim().optional(),
  isActive: yup.boolean().default(true),
});

export type PolicyRuleFormValues = yup.InferType<typeof policyRuleSchema>;

export const POLICY_RULE_FORM_DEFAULTS: PolicyRuleFormValues = {
  category: "",
  gradeApplicable: "",
  maxAmount: undefined as unknown as number,
  maxAmountUnit: "",
  autoApproveLimit: undefined,
  requiresReceiptAbove: undefined as unknown as number,
  effectiveFrom: "",
  description: "",
  isActive: true,
};
