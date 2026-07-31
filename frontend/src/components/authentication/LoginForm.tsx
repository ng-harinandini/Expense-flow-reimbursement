'use client';

import Link from 'next/link';
import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useForm } from 'react-hook-form';
import { yupResolver } from '@hookform/resolvers/yup';
import { Lock, Mail } from 'lucide-react';

import { loginSchema, type LoginValues } from '@/components/authentication/schemas';
import { login, NEW_PASSWORD_REQUIRED } from '@/api/auth';
import { useUser } from '@/context/UserContext';
import { useToast } from '@/components/ui/toast';
import { getRedirectPathForRole } from '@/lib/roleConstants';
import { getErrorMessage } from '@/lib/apiError';
import { CHALLENGE_STORAGE_KEY, saveSession } from '@/lib/authStorage';
import { AuthCard } from './AuthCard';
import { AuthField } from './AuthField';
import { AuthSubmitButton } from './AuthSubmitButton';

export default function LoginForm() {
  const toast = useToast();
  const [rememberMe, setRememberMe] = useState(false);
  const router = useRouter();
  const { setUser } = useUser();

  const {
    register,
    handleSubmit,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<LoginValues>({
    resolver: yupResolver(loginSchema),
    defaultValues: { email: '', password: '' },
  });

  useEffect(() => {
    const savedEmail = localStorage.getItem('rememberMeEmail');
    if (savedEmail) {
      setValue('email', savedEmail);
      setRememberMe(true);
    }
  }, [setValue]);

  const onSubmit = async ({ email, password }: LoginValues) => {
    try {
      const data = await login(email, password);

      // An admin-created user's first login returns a challenge instead of tokens.
      if (data.challenge === NEW_PASSWORD_REQUIRED && data.session) {
        sessionStorage.setItem(
          CHALLENGE_STORAGE_KEY,
          JSON.stringify({ email, session: data.session })
        );
        toast({ message: 'Please set a new password to continue.', type: 'info' });
        router.push('/set-new-password');
        return;
      }

      if (!data.accessToken || !data.user) {
        toast({ message: 'Unexpected response from the server. Please try again.', type: 'error' });
        return;
      }

      if (rememberMe) {
        localStorage.setItem('rememberMeEmail', email);
      } else {
        localStorage.removeItem('rememberMeEmail');
      }

      saveSession({
        accessToken: data.accessToken,
        user: data.user,
        expiresIn: data.expiresIn,
      });
      setUser(data.user);

      toast({ message: 'Logged in successfully.', type: 'success' });
      router.push(getRedirectPathForRole(data.user.role));
    } catch (err) {
      toast({
        message: getErrorMessage(err, 'Login failed. Please try again.'),
        type: 'error',
      });
    }
  };

  return (
    <AuthCard
      image={{ src: '/assets/login.png', alt: '' }}
      title={
        <>
          Welcome Back! <span aria-hidden>👋</span>
        </>
      }
      subtitle='Login to continue to your account'
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

        <AuthField
          icon={Lock}
          revealToggle
          id='password'
          placeholder='Password'
          autoCapitalize='none'
          autoComplete='current-password'
          disabled={isSubmitting}
          error={errors.password?.message}
          {...register('password')}
        />

        <div className='flex items-center justify-between'>
          <label htmlFor='remember' className='flex cursor-pointer items-center gap-2'>
            <span className='relative inline-flex items-center'>
              <input
                type='checkbox'
                id='remember'
                checked={rememberMe}
                onChange={(e) => setRememberMe(e.target.checked)}
                className='peer size-4 shrink-0 cursor-pointer appearance-none rounded border border-slate-300 transition-colors checked:border-primary checked:bg-primary'
              />
              <svg
                className='pointer-events-none absolute inset-0 m-auto size-3 text-white opacity-0 peer-checked:opacity-100'
                viewBox='0 0 24 24'
                fill='none'
                stroke='currentColor'
                strokeWidth='3'
                strokeLinecap='round'
                strokeLinejoin='round'
              >
                <polyline points='20 6 9 17 4 12' />
              </svg>
            </span>
            <span className='text-sm text-slate-600'>Remember me</span>
          </label>

          <Link
            href='/forgot-password'
            className='text-sm font-medium text-secondary transition-colors hover:text-secondary/80'
          >
            Forgot password?
          </Link>
        </div>

        <AuthSubmitButton disabled={isSubmitting}>
          {isSubmitting ? 'Signing in...' : 'Sign In'}
        </AuthSubmitButton>
      </form>

      {/* Accounts are created by an administrator — there is no self-service signup. */}
      <p className='mt-4 text-center text-sm text-slate-500'>
        Need an account? Contact your administrator.
      </p>
    </AuthCard>
  );
}
