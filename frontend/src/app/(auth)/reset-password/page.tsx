import type { Metadata } from 'next';
import ResetPasswordForm from '@/components/authentication/ResetPasswordForm';
import { Suspense } from 'react';

export const metadata: Metadata = {
  title: 'Reset Password',
  description: 'Reset the password for your ExpenseFlow account.',
};

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={<div>Loading...</div>}>
      <ResetPasswordForm />
    </Suspense>
  );
}
