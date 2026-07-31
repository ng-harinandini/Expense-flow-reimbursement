'use client';

import { cn } from '@/lib/utils';

/** The full-width orange→purple gradient button shared by every auth form. */
export function AuthSubmitButton({
  children,
  className,
  ...props
}: React.ComponentProps<'button'>) {
  return (
    <button
      type='submit'
      {...props}
      className={cn(
        'h-11 w-full rounded-xl bg-gradient-to-r from-primary to-secondary text-sm font-semibold text-white',
        'shadow-lg shadow-secondary/25 transition-opacity hover:opacity-95',
        'disabled:cursor-not-allowed disabled:opacity-60',
        className
      )}
    >
      {children}
    </button>
  );
}
