import * as yup from "yup";

export const CATEGORY_FIELD_DATA_TYPES = ["text", "number", "date", "boolean", "enum"] as const;

export const categoryFieldSchema = yup.object({
  name: yup
    .string()
    .trim()
    .required("Field name is required")
    .matches(/^[a-z][a-z0-9_]*$/, "Use snake_case, e.g. travel_route"),
  label: yup.string().trim().required("Label is required"),
  description: yup.string().trim().optional(),
  dataType: yup
    .mixed<(typeof CATEGORY_FIELD_DATA_TYPES)[number]>()
    .oneOf(CATEGORY_FIELD_DATA_TYPES)
    .required(),
  options: yup
    .array()
    .of(yup.string().trim().required())
    .when("dataType", {
      is: "enum",
      then: (schema) => schema.min(1, "Add at least one option for an enum field"),
      otherwise: (schema) => schema.optional(),
    }),
  required: yup.boolean().default(false),
});

export const categorySchema = yup.object({
  code: yup
    .string()
    .trim()
    .required("Code is required")
    .matches(/^[A-Z][A-Z0-9_]*$/, "Use UPPER_SNAKE_CASE, e.g. AIR_TRAVEL"),
  name: yup.string().trim().required("Name is required"),
  description: yup.string().trim().optional(),
  displayOrder: yup
    .number()
    .typeError("Display order must be a number")
    .min(0, "Display order cannot be negative")
    .required("Display order is required"),
  isActive: yup.boolean().default(true),
  customFields: yup.array().of(categoryFieldSchema).default([]),
});

export type CategoryFieldFormValues = yup.InferType<typeof categoryFieldSchema>;
export type CategoryFormValues = yup.InferType<typeof categorySchema>;

export const CATEGORY_FORM_DEFAULTS: CategoryFormValues = {
  code: "",
  name: "",
  description: "",
  displayOrder: 100,
  isActive: true,
  customFields: [],
};

export const EMPTY_CATEGORY_FIELD: CategoryFieldFormValues = {
  name: "",
  label: "",
  description: "",
  dataType: "text",
  options: [],
  required: false,
};
