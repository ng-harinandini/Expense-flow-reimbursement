import * as yup from "yup";

import type { EmployeeGrade, EmployeeStatus, UserRole } from "@/types";

import { EMPLOYEE_GRADES, EMPLOYEE_STATUSES, USER_ROLES } from "./helpers";

export const employeeSchema = yup.object({
  name: yup.string().trim().required("Employee name is required"),
  email: yup
    .string()
    .trim()
    .lowercase()
    .email("Enter a valid email address")
    .required("Email is required"),
  grade: yup
    .mixed<EmployeeGrade>()
    .oneOf(EMPLOYEE_GRADES, "Grade is required")
    .required("Grade is required"),
  role: yup
    .mixed<UserRole>()
    .oneOf(USER_ROLES, "Role is required")
    .required("Role is required"),
  status: yup
    .mixed<EmployeeStatus>()
    .oneOf(EMPLOYEE_STATUSES, "Status is required")
    .required("Status is required"),
  managerId: yup.string().default(""),
});

export type EmployeeFormValues = yup.InferType<typeof employeeSchema>;

export const EMPLOYEE_FORM_DEFAULTS: EmployeeFormValues = {
  name: "",
  email: "",
  grade: "" as unknown as EmployeeGrade,
  role: "" as unknown as UserRole,
  status: "active",
  managerId: "",
};
