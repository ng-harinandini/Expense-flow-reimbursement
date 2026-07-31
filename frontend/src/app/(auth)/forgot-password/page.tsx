import type { Metadata } from 'next';
import ForgotPasswordForm from '@/components/authentication/ForgotPasswordForm';
import { Suspense } from 'react';

export const metadata: Metadata = {
  title: 'Forgot Password',
  description: 'Request a password reset code for your ExpenseFlow account.',
};

export default function ForgotPasswordPage() {
  return (
    <Suspense fallback={<div>Loading...</div>}>
      <ForgotPasswordForm />
    </Suspense>
  );
}
