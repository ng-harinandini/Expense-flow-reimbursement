'use client';

import Link from 'next/link';
import { useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useForm } from 'react-hook-form';
import { yupResolver } from '@hookform/resolvers/yup';
import { Mail } from 'lucide-react';

import { forgotPasswordSchema, type ForgotPasswordValues } from '@/components/authentication/schemas';
import { forgotPassword } from '@/api/auth';
import { useToast } from '@/components/ui/toast';
import { getErrorMessage } from '@/lib/apiError';
import { AuthCard } from './AuthCard';
import { AuthField } from './AuthField';
import { AuthSubmitButton } from './AuthSubmitButton';

export default function ForgotPasswordForm() {
  const toast = useToast();
  const router = useRouter();
  const searchParams = useSearchParams();

  const {
    register,
    handleSubmit,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<ForgotPasswordValues>({
    resolver: yupResolver(forgotPasswordSchema),
    defaultValues: { email: '' },
  });

  useEffect(() => {
    const emailParam = searchParams.get('email');
    if (emailParam) {
      setValue('email', emailParam);
    }
  }, [searchParams, setValue]);

  const onSubmit = async ({ email }: ForgotPasswordValues) => {
    try {
      const data = await forgotPassword(email);
      toast({ message: data.detail, type: 'success' });
      router.push(`/reset-password?email=${encodeURIComponent(email)}`);
    } catch (err) {
      toast({
        message: getErrorMessage(err, 'Failed to send reset code. Please try again.'),
        type: 'error',
      });
    }
  };

  return (
    <AuthCard
      image={{ src: '/assets/forgot-password.png', alt: '' }}
      title='Forgot Password?'
      subtitle="Enter your email address and we'll send you a reset code."
      backHref='/login'
    >
      <form className='space-y-3' onSubmit={handleSubmit(onSubmit)} noValidate>
        <AuthField
          icon={Mail}
          type='email'
          id='email'
          placeholder='Email address'
          autoCapitalize='none'
          autoComplete='email'
          disabled={isSubmitting}
          error={errors.email?.message}
          {...register('email')}
        />

        <AuthSubmitButton disabled={isSubmitting}>
          {isSubmitting ? 'Sending...' : 'Send Reset Code'}
        </AuthSubmitButton>
      </form>

      <p className='mt-4 text-center'>
        <Link
          href='/login'
          className='text-sm font-semibold text-secondary transition-colors hover:text-secondary/80'
        >
          Back to Login
        </Link>
      </p>
    </AuthCard>
  );
}
