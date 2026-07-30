import type { Metadata } from 'next';
import LoginForm from '@/components/authentication/LoginForm';

export const metadata: Metadata = {
  title: 'Login',
  description: 'Sign in to your ExpenseFlow account.',
};

function page() {
  return <LoginForm />;
}

export default page;
