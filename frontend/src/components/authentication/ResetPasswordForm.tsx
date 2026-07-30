'use client';

import Link from 'next/link';
import { useState, useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useForm } from 'react-hook-form';
import { yupResolver } from '@hookform/resolvers/yup';
import { Check, KeyRound, Lock, Mail, ShieldCheck } from 'lucide-react';

import { resetPasswordSchema, type ResetPasswordValues } from '@/components/authentication/schemas';
import { confirmForgotPassword } from '@/api/auth';
import { useToast } from '@/components/ui/toast';
import { getErrorMessage } from '@/lib/apiError';
import { AuthCard } from './AuthCard';
import { AuthField } from './AuthField';
import { AuthSubmitButton } from './AuthSubmitButton';
import { PasswordStrength } from './PasswordStrength';

export default function ResetPasswordForm() {
  const [emailPrefilled, setEmailPrefilled] = useState(false);
  const router = useRouter();
  const toast = useToast();
  const searchParams = useSearchParams();

  const {
    register,
    handleSubmit,
    watch,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<ResetPasswordValues>({
    resolver: yupResolver(resetPasswordSchema),
    defaultValues: { email: '', code: '', newPassword: '', confirmPassword: '' },
  });

  const newPassword = watch('newPassword');

  useEffect(() => {
    const emailParam = searchParams.get('email');
    if (emailParam) {
      setValue('email', emailParam);
      setEmailPrefilled(true);
    }
  }, [searchParams, setValue]);

  const onSubmit = async (values: ResetPasswordValues) => {
    try {
      const data = await confirmForgotPassword({
        email: values.email,
        code: values.code,
        newPassword: values.newPassword,
      });
      toast({ message: data.detail, type: 'success' });
      router.push('/login');
    } catch (err) {
      toast({
        message: getErrorMessage(err, 'Failed to reset password. Please try again.'),
        type: 'error',
      });
    }
  };

  return (
    <AuthCard
      image={{ src: '/assets/reset-password.png', alt: '' }}
      title='Reset Password'
      subtitle='Enter the code sent to your email and your new password.'
      backHref='/forgot-password'
      wide
    >
      <form className='space-y-3' onSubmit={handleSubmit(onSubmit)} noValidate>
        <AuthField
          icon={Mail}
          trailingIcon={emailPrefilled ? Check : undefined}
          trailingValid={emailPrefilled}
          type='email'
          id='email'
          placeholder='Email address'
          autoCapitalize='none'
          autoComplete='email'
          disabled={isSubmitting}
          error={errors.email?.message}
          {...register('email')}
        />

        <AuthField
          icon={ShieldCheck}
          trailingIcon={KeyRound}
          type='text'
          id='code'
          placeholder='Confirmation code'
          autoCapitalize='none'
          autoComplete='one-time-code'
          inputMode='numeric'
          disabled={isSubmitting}
          error={errors.code?.message}
          {...register('code')}
        />

        <AuthField
          icon={Lock}
          revealToggle
          id='newPassword'
          placeholder='New password'
          autoCapitalize='none'
          autoComplete='new-password'
          disabled={isSubmitting}
          error={errors.newPassword?.message}
          {...register('newPassword')}
        />

        <AuthField
          icon={Lock}
          revealToggle
          id='confirmPassword'
          placeholder='Confirm new password'
          autoCapitalize='none'
          autoComplete='new-password'
          disabled={isSubmitting}
          error={errors.confirmPassword?.message}
          {...register('confirmPassword')}
        />

        <PasswordStrength password={newPassword} />

        <AuthSubmitButton disabled={isSubmitting} className='!mt-4'>
          {isSubmitting ? 'Resetting...' : 'Reset Password'}
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
