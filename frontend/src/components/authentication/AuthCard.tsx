'use client';

import Image from 'next/image';
import Link from 'next/link';
import { ArrowLeft } from 'lucide-react';

import { cn } from '@/lib/utils';

/**
 * How much of each source PNG's height the actual artwork occupies — the rest is
 * transparent padding baked into the export. Measured from each file's alpha bounding
 * box, and used to scale every illustration up to the same ~90% visual fill.
 */
const ART_HEIGHT_FILL: Record<string, number> = {
  '/assets/login.png': 0.561,
  '/assets/forgot-password.png': 0.401,
  '/assets/reset-password.png': 0.491,
};

const TARGET_FILL = 0.9;

interface AuthCardProps {
  /** Illustration shown above the heading. */
  image: { src: string; alt: string };
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  /** Renders a circular back button in the top-left corner. */
  backHref?: string;
  /** Wider card for the longer reset/set-password forms. */
  wide?: boolean;
  children: React.ReactNode;
}

/**
 * The shared shell for every auth screen: centred white card on a lavender page,
 * with the illustration, heading and optional back button laid out consistently.
 */
export function AuthCard({
  image,
  title,
  subtitle,
  backHref,
  wide = false,
  children,
}: AuthCardProps) {
  return (
    <main className='flex min-h-screen items-center justify-center bg-[#f4f0fd] p-4'>
      <div
        className={cn(
          'relative w-full rounded-2xl bg-white px-7 py-7 shadow-[0_20px_60px_-15px_rgba(102,91,216,0.25)]',
          wide ? 'max-w-md' : 'max-w-sm'
        )}
      >
        {backHref && (
          <Link
            href={backHref}
            aria-label='Go back'
            className='absolute left-5 top-5 flex size-8 items-center justify-center rounded-full bg-[#f4f0fd] text-secondary transition-colors hover:bg-secondary/15'
          >
            <ArrowLeft className='size-4' />
          </Link>
        )}

        <div className='flex flex-col items-center text-center'>
          {/* The art is scaled up inside a fixed-height, clipped box: it fills the slack left
              by each PNG's transparent padding without affecting the card's height. */}
          <div className='flex h-20 w-full items-center justify-center overflow-hidden'>
            <Image
              src={image.src}
              alt={image.alt}
              width={320}
              height={213}
              priority
              className='h-full w-auto object-contain'
              style={{
                transform: `scale(${(
                  TARGET_FILL / (ART_HEIGHT_FILL[image.src] ?? TARGET_FILL)
                ).toFixed(3)})`,
              }}
            />
          </div>
          <h1 className='mt-1.5 text-xl font-bold tracking-tight text-slate-900'>{title}</h1>
          {subtitle && (
            <p className='mt-1 text-[13px] leading-snug text-slate-500'>{subtitle}</p>
          )}
        </div>

        <div className='mt-5'>{children}</div>
      </div>
    </main>
  );
}
