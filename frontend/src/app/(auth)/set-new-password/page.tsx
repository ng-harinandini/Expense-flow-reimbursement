import type { Metadata } from 'next';
import SetNewPasswordForm from '@/components/authentication/SetNewPasswordForm';

export const metadata: Metadata = {
  title: 'Set New Password',
  description: 'Set a new password for your ExpenseFlow account.',
};

export default function SetNewPasswordPage() {
  return <SetNewPasswordForm />;
}
