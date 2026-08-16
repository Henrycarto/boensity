'use client'

/**
 * Which scanner produced a scan.
 *
 * Present on every scan row and every extraction, not as a label but as a
 * warning surface. A trajectory built across two manufacturers carries a
 * residual step that no standardisation fully removes, and the physician
 * reading the chart has to be able to see where the hardware changed.
 */

import type { Manufacturer } from '@boensity/shared-types'
import { MANUFACTURER_LABEL } from '@boensity/shared-types'
import { cn } from '@/lib/utils'

interface ManufacturerBadgeProps {
  manufacturer: Manufacturer
  scannerModel?: string | null
  standardized?: boolean
  className?: string
}

const STYLES: Record<Manufacturer, { border: string; text: string }> = {
  hologic: { border: '#3A424E', text: '#8B949E' },
  ge_lunar: { border: '#3A424E', text: '#8B949E' },
  // Unknown is the only state that matters visually: it means the values could
  // not be standardised and the scan needs a human before it is trusted.
  unknown: { border: '#D29922', text: '#D29922' },
}

export function ManufacturerBadge({
  manufacturer,
  scannerModel,
  standardized,
  className,
}: ManufacturerBadgeProps) {
  const style = STYLES[manufacturer]

  return (
    <span
      className={cn(
        'inline-flex items-center gap-2 border px-2 py-[2px] text-[10px] uppercase tracking-[0.1em]',
        className,
      )}
      style={{ borderColor: style.border, color: style.text }}
      title={
        manufacturer === 'unknown'
          ? 'Manufacturer not identified. Values could not be standardised across scanners.'
          : `${MANUFACTURER_LABEL[manufacturer]}${scannerModel ? ` ${scannerModel}` : ''}`
      }
    >
      {MANUFACTURER_LABEL[manufacturer]}
      {scannerModel && <span className="text-ink-faint normal-case">{scannerModel}</span>}
      {standardized === false && manufacturer !== 'unknown' && (
        <span style={{ color: '#D29922' }}>not standardised</span>
      )}
    </span>
  )
}

export default ManufacturerBadge
