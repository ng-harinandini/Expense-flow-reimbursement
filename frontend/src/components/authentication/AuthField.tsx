'use client';

import * as React from 'react';
import { Eye, EyeOff, type LucideIcon } from 'lucide-react';

import { cn } from '@/lib/utils';

interface AuthFieldProps extends Omit<React.ComponentProps<'input'>, 'className'> {
  /** Icon rendered inside the field's left edge. */
  icon: LucideIcon;
  /** Static icon on the right (ignored when `revealToggle` is set). */
  trailingIcon?: LucideIcon;
  /** Highlights the trailing icon green — used to confirm a prefilled email. */
  trailingValid?: boolean;
  /** Turns the field into a password input with a show/hide toggle. */
  revealToggle?: boolean;
  /** Validation message from react-hook-form; renders below the field. */
  error?: string;
}

/**
 * The rounded, icon-flanked input used across the auth screens.
 * Owns its own show/hide state so each form doesn't repeat that wiring.
 *
 * Forwards its ref so `{...register(name)}` can spread straight onto it.
 */
export const AuthField = React.forwardRef<HTMLInputElement, AuthFieldProps>(function AuthField(
  {
    icon: Icon,
    trailingIcon: TrailingIcon,
    trailingValid = false,
    revealToggle = false,
    disabled,
    error,
    ...props
  },
  ref
) {
  const [revealed, setRevealed] = React.useState(false);
  const RevealIcon = revealed ? EyeOff : Eye;
  const errorId = error && props.id ? `${props.id}-error` : undefined;

  return (
    <div className='space-y-1'>
      <div className='relative'>
        <Icon className='pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400' />
        <input
          {...props}
          ref={ref}
          type={revealToggle ? (revealed ? 'text' : 'password') : props.type}
          disabled={disabled}
          aria-invalid={error ? true : undefined}
          aria-describedby={errorId}
          className={cn(
            'h-11 w-full rounded-xl border bg-white pl-10 pr-10 text-sm text-slate-900',
            'placeholder:text-slate-400',
            'transition-colors outline-none',
            error
              ? 'border-destructive focus:border-destructive focus:ring-2 focus:ring-destructive/20'
              : 'border-slate-200 focus:border-secondary focus:ring-2 focus:ring-secondary/20',
            'disabled:cursor-not-allowed disabled:opacity-60'
          )}
        />
        {revealToggle ? (
          <button
            type='button'
            onClick={() => setRevealed((v) => !v)}
            disabled={disabled}
            aria-label={revealed ? 'Hide password' : 'Show password'}
            className='absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 transition-colors hover:text-slate-600 disabled:cursor-not-allowed'
          >
            <RevealIcon className='size-4' />
          </button>
        ) : (
          TrailingIcon && (
            <TrailingIcon
              className={cn(
                'pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2',
                trailingValid ? 'text-emerald-500' : 'text-slate-400'
              )}
            />
          )
        )}
      </div>
      {error && (
        <p id={errorId} className='pl-1 text-xs text-destructive'>
          {error}
        </p>
      )}
    </div>
  );
});
