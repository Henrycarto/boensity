'use client'

/**
 * Shared primitives.
 *
 * Deliberately few. Every one is squared, bordered, and flat, because the
 * reference point is a medical device panel rather than a web application.
 */

import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import type { AlertSeverity, DiagnosticBand } from '@boensity/shared-types'
import { BAND_COLOR, BAND_LABEL } from '@boensity/shared-types'
import { cn } from '@/lib/utils'

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------

export function Panel({
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn('border border-line bg-surface', className)} {...props}>
      {children}
    </div>
  )
}

export function PanelHeader({
  title,
  action,
  className,
}: {
  title: string
  action?: React.ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'flex items-baseline justify-between border-b border-line px-4 py-3',
        className,
      )}
    >
      <h2 className="text-[11px] uppercase tracking-[0.14em] text-ink-faint">{title}</h2>
      {action}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Button
// ---------------------------------------------------------------------------

type ButtonVariant = 'primary' | 'secondary' | 'ghost'

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  asChild?: boolean
}

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: 'border-danger bg-danger text-[#0D1117] hover:bg-[#F06A36]',
  secondary: 'border-line-strong bg-transparent text-ink hover:border-ink-muted',
  ghost: 'border-transparent bg-transparent text-ink-muted hover:text-ink',
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = 'secondary', asChild = false, ...props }, ref) => {
    const Component = asChild ? Slot : 'button'
    return (
      <Component
        ref={ref}
        className={cn(
          // Square corners. A pill button would read as marketing.
          'inline-flex items-center justify-center border px-4 py-2 text-sm font-medium',
          'transition-colors disabled:cursor-not-allowed disabled:opacity-40',
          BUTTON_VARIANTS[variant],
          className,
        )}
        {...props}
      />
    )
  },
)
Button.displayName = 'Button'

// ---------------------------------------------------------------------------
// Clinical value
// ---------------------------------------------------------------------------

/** A T-score, coloured by its WHO band. Mono, tabular, always signed. */
export function TScoreValue({
  value,
  size = 'base',
  className,
}: {
  value: number
  size?: 'sm' | 'base' | 'lg'
  className?: string
}) {
  const band: DiagnosticBand =
    value <= -2.5 ? 'osteoporosis' : value <= -1.0 ? 'osteopenia' : 'normal'

  const sizes = { sm: 'text-xs', base: 'text-sm', lg: 'text-2xl' }

  return (
    <span
      className={cn('mono tabular-nums', sizes[size], className)}
      style={{ color: BAND_COLOR[band] }}
      title={BAND_LABEL[band]}
    >
      {value > 0 ? `+${value.toFixed(1)}` : value.toFixed(1)}
    </span>
  )
}

export function BandPill({ band, className }: { band: DiagnosticBand; className?: string }) {
  return (
    <span
      className={cn(
        'inline-flex items-center border px-2 py-[2px] text-[10px] uppercase tracking-[0.1em]',
        className,
      )}
      style={{ borderColor: BAND_COLOR[band], color: BAND_COLOR[band] }}
    >
      {BAND_LABEL[band]}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Alerts
// ---------------------------------------------------------------------------

const SEVERITY_COLOR: Record<AlertSeverity, string> = {
  critical: '#E05C2A',
  warning: '#D29922',
  info: '#8B949E',
}

export function AlertRow({
  severity,
  title,
  detail,
}: {
  severity: AlertSeverity
  title: string
  detail: string
}) {
  return (
    <li className="flex gap-3 border-b border-line px-4 py-3 last:border-b-0">
      {/* A bar rather than an icon. Icons at this size become decoration. */}
      <span
        className="mt-[3px] w-[2px] shrink-0 self-stretch"
        style={{ backgroundColor: SEVERITY_COLOR[severity] }}
        aria-hidden
      />
      <div className="min-w-0">
        <p className="text-sm text-ink" style={{ color: SEVERITY_COLOR[severity] }}>
          {title}
        </p>
        <p className="mt-1 text-xs leading-relaxed text-ink-muted">{detail}</p>
      </div>
    </li>
  )
}

// ---------------------------------------------------------------------------
// Misc
// ---------------------------------------------------------------------------

export function Label({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <span className={cn('text-[11px] uppercase tracking-[0.14em] text-ink-faint', className)}>
      {children}
    </span>
  )
}

export function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="border border-dashed border-line px-6 py-10 text-center">
      <p className="text-sm text-ink-muted">{title}</p>
      <p className="mx-auto mt-2 max-w-md text-xs leading-relaxed text-ink-faint">{detail}</p>
    </div>
  )
}

/** Confidence rendered as a bar, since a bare 0.87 means nothing to a reader. */
export function ConfidenceBar({ value, className }: { value: number; className?: string }) {
  const percent = Math.round(value * 100)
  const color = value >= 0.9 ? '#238636' : value >= 0.8 ? '#D29922' : '#E05C2A'

  return (
    <span className={cn('inline-flex items-center gap-2', className)}>
      <span className="h-[3px] w-16 bg-line" aria-hidden>
        <span
          className="block h-full"
          style={{ width: `${percent}%`, backgroundColor: color }}
        />
      </span>
      <span className="mono text-[11px]" style={{ color }}>
        {percent}%
      </span>
    </span>
  )
}
