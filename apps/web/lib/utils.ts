import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}

/** Scan dates render in a fixed unambiguous format everywhere.
 *  Locale formatting would show a European clinician a US ordering. */
export function formatScanDate(value: string | Date): string {
  const date = typeof value === 'string' ? new Date(value) : value
  if (Number.isNaN(date.getTime())) return 'Unknown'
  const month = date.toLocaleString('en-GB', { month: 'short' })
  return `${String(date.getDate()).padStart(2, '0')} ${month} ${date.getFullYear()}`
}

export function formatTScore(value: number): string {
  return value > 0 ? `+${value.toFixed(1)}` : value.toFixed(1)
}

export function yearsBetween(from: string | Date, to: string | Date): number {
  const start = typeof from === 'string' ? new Date(from) : from
  const end = typeof to === 'string' ? new Date(to) : to
  return (end.getTime() - start.getTime()) / (365.2425 * 24 * 60 * 60 * 1000)
}
