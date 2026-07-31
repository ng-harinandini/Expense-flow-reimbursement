'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useForm } from 'react-hook-form';
import { yupResolver } from '@hookform/resolvers/yup';
import { Lock } from 'lucide-react';

import { setNewPasswordSchema, type SetNewPasswordValues } from '@/components/authentication/schemas';
import { respondChallenge } from '@/api/auth';
import { useUser } from '@/context/UserContext';
import { useToast } from '@/components/ui/toast';
import { getRedirectPathForRole } from '@/lib/roleConstants';
import { getErrorMessage } from '@/lib/apiError';
import { CHALLENGE_STORAGE_KEY } from '@/lib/authStorage';
import { AuthCard } from './AuthCard';
import { AuthField } from './AuthField';
import { AuthSubmitButton } from './AuthSubmitButton';
import { PasswordStrength } from './PasswordStrength';

export default function SetNewPasswordForm() {
  const [challenge, setChallenge] = useState<{ email: string; session: string } | null>(null);
  const router = useRouter();
  const toast = useToast();
  const { signIn } = useUser();

  const {
    register,
    handleSubmit,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<SetNewPasswordValues>({
    resolver: yupResolver(setNewPasswordSchema),
    defaultValues: { newPassword: '', confirmPassword: '' },
  });

  const newPassword = watch('newPassword');

  useEffect(() => {
    const stored = sessionStorage.getItem(CHALLENGE_STORAGE_KEY);
    if (!stored) {
      router.replace('/login');
      return;
    }
    try {
      const parsed = JSON.parse(stored);
      if (!parsed?.email || !parsed?.session) {
        router.replace('/login');
        return;
      }
      setChallenge(parsed);
    } catch {
      router.replace('/login');
    }
  }, [router]);

  const onSubmit = async (values: SetNewPasswordValues) => {
    if (!challenge) return;

    try {
      const data = await respondChallenge({
        email: challenge.email,
        session: challenge.session,
        newPassword: values.newPassword,
      });

      if (!data.accessToken || !data.user) {
        // A follow-on challenge, or an unexpected shape: the stored session is
        // spent either way, so send them back to log in fresh.
        sessionStorage.removeItem(CHALLENGE_STORAGE_KEY);
        toast({ message: 'Could not complete sign-in. Please log in again.', type: 'error' });
        router.replace('/login');
        return;
      }

      sessionStorage.removeItem(CHALLENGE_STORAGE_KEY);
      signIn({
        accessToken: data.accessToken,
        user: data.user,
        expiresIn: data.expiresIn,
      });

      toast({ message: 'Password set successfully.', type: 'success' });
      router.push(getRedirectPathForRole(data.user.role));
    } catch (err) {
      toast({
        message: getErrorMessage(err, 'Failed to set new password. Please try again.'),
        type: 'error',
      });
    }
  };

  if (!challenge) {
    return null;
  }

  return (
    <AuthCard
      image={{ src: '/assets/reset-password.png', alt: '' }}
      title='Set New Password'
      subtitle='This is your first login. Please set a new password to continue.'
      wide
    >
      <div className='mb-4 rounded-xl bg-[#f4f0fd] px-4 py-2.5 text-center text-sm break-all text-slate-600'>
        {challenge.email}
      </div>

      <form className='space-y-3' onSubmit={handleSubmit(onSubmit)} noValidate>
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
          {isSubmitting ? 'Setting password...' : 'Set Password'}
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
