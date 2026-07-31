'use client';

import { cn } from '@/lib/utils';
import { scorePassword, STRENGTH_LABELS } from '@/components/authentication/schemas';

const SEGMENT_COLORS = ['bg-red-500', 'bg-orange-500', 'bg-fuchsia-500', 'bg-secondary'];
const LABEL_COLORS = ['text-red-600', 'text-orange-600', 'text-fuchsia-600', 'text-secondary'];

/**
 * Four-segment strength meter. Scored against the same rules the form validates with,
 * so a bar reading "Strong" can never belong to a password submit would reject.
 */
export function PasswordStrength({ password }: { password: string }) {
  if (!password) return null;

  const score = scorePassword(password); // 1..4
  const index = score - 1;

  return (
    <div className='space-y-1.5'>
      <p className='text-xs text-slate-500'>
        Password strength:{' '}
        <span className={cn('font-semibold', LABEL_COLORS[index])}>
          {STRENGTH_LABELS[index]}
        </span>
      </p>
      <div className='flex gap-1.5' role='presentation'>
        {SEGMENT_COLORS.map((color, i) => (
          <span
            key={i}
            className={cn(
              'h-1 flex-1 rounded-full transition-colors',
              i < score ? color : 'bg-slate-200'
            )}
          />
        ))}
      </div>
    </div>
  );
}
