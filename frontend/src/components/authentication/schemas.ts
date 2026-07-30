import * as yup from 'yup';

const PASSWORD_MIN_LENGTH = 8;

const emailSchema = yup
  .string()
  .trim()
  .required('Email is required.')
  .matches(/^[^\s@]+@[^\s@]+\.[^\s@]+$/, 'Enter a valid email address.');


const passwordSchema = (label: string) =>
  yup
    .string()
    .required(`${label} is required.`)
    .min(PASSWORD_MIN_LENGTH, `Password must be at least ${PASSWORD_MIN_LENGTH} characters.`)
    .matches(/[a-z]/, 'Password must include a lowercase letter.')
    .matches(/[A-Z]/, 'Password must include an uppercase letter.')
    .matches(/\d/, 'Password must include a number.')
    .matches(/[^A-Za-z0-9]/, 'Password must include a special character.');

const confirmPasswordSchema = yup
  .string()
  .required('Please confirm your password.')
  .oneOf([yup.ref('newPassword')], 'Passwords do not match.');

export const loginSchema = yup.object({
  email: emailSchema,
  password: yup.string().required('Password is required.'),
});

export const forgotPasswordSchema = yup.object({
  email: emailSchema,
});

export const setNewPasswordSchema = yup.object({
  newPassword: passwordSchema('New password'),
  confirmPassword: confirmPasswordSchema,
});

export const resetPasswordSchema = yup.object({
  email: emailSchema,
  code: yup.string().trim().required('Confirmation code is required.'),
  newPassword: passwordSchema('New password'),
  confirmPassword: confirmPasswordSchema,
});

export type LoginValues = yup.InferType<typeof loginSchema>;
export type ForgotPasswordValues = yup.InferType<typeof forgotPasswordSchema>;
export type SetNewPasswordValues = yup.InferType<typeof setNewPasswordSchema>;
export type ResetPasswordValues = yup.InferType<typeof resetPasswordSchema>;

export const STRENGTH_LABELS = ['Weak', 'Fair', 'Good', 'Strong'] as const;


export function scorePassword(password: string): 1 | 2 | 3 | 4 {
  let met = 0;
  if (password.length >= PASSWORD_MIN_LENGTH) met++;
  if (/[a-z]/.test(password) && /[A-Z]/.test(password)) met++;
  if (/\d/.test(password)) met++;
  if (/[^A-Za-z0-9]/.test(password)) met++;

  // All rules satisfied but still short: "Good", not "Strong".
  if (met === 4 && password.length < 12) return 3;
  return Math.max(1, met) as 1 | 2 | 3 | 4;
}
