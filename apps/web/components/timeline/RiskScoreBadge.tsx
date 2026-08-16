'use client'

/**
 * The ten year FRAX probability, and what it becomes on treatment.
 *
 * Two numbers. That is the entire physician argument, and adding a third
 * weakens it. "Reduces fracture risk by 20 percent" is a statistic. "24
 * percent becomes 19 percent" is a decision, because the second number is
 * what the patient actually lives with.
 */

import type { FraxResponse } from '@boensity/shared-types'
import { FRAX_HIP_THRESHOLD, FRAX_MAJOR_THRESHOLD } from '@boensity/shared-types'

interface RiskScoreBadgeProps {
  frax: FraxResponse
  className?: string
}

export function RiskScoreBadge({ frax, className }: RiskScoreBadgeProps) {
  const elevated = frax.intervention_threshold_met
  const accent = elevated ? '#E05C2A' : '#E6EDF3'
  const treatment = frax.treatment

  return (
    <section className={`panel ${className ?? ''}`}>
      <header className="panel-header">
        <h2 className="label">10 year fracture probability</h2>
        <span className="label">{frax.bmd_included ? 'With BMD' : 'Without BMD'}</span>
      </header>

      <div className="px-4 py-5">
        <div className="flex items-start justify-between gap-6">
          <div>
            <div className="mono text-metric" style={{ color: accent }}>
              {frax.major_osteoporotic_percent.toFixed(1)}
              <span className="text-2xl">%</span>
            </div>
            <p className="mt-1 text-xs text-ink-muted">Major osteoporotic fracture</p>
          </div>

          <div className="text-right">
            <div className="mono text-metric-sm" style={{ color: accent }}>
              {frax.hip_percent.toFixed(1)}
              <span className="text-base">%</span>
            </div>
            <p className="mt-1 text-xs text-ink-muted">Hip fracture</p>
          </div>
        </div>

        {/* The second number. Placed directly under the first, on one line,
            because the comparison is the point and separating them into two
            cards would break it. */}
        {treatment && (
          <div className="mt-5 border-t border-line pt-4">
            <p className="text-sm text-ink">
              <span className="text-ink-muted">On treatment: </span>
              <span className="mono" style={{ color: '#238636' }}>
                {treatment.major_osteoporotic_percent.toFixed(1)}%
              </span>
              <span className="text-ink-muted"> major, </span>
              <span className="mono" style={{ color: '#238636' }}>
                {treatment.hip_percent.toFixed(1)}%
              </span>
              <span className="text-ink-muted"> hip</span>
            </p>
            <p className="mt-2 text-xs text-ink-faint">
              {treatment.agent}
              {treatment.number_needed_to_treat_major !== null && (
                <>
                  {' '}
                  &middot; Number needed to treat{' '}
                  <span className="mono text-ink-muted">
                    {treatment.number_needed_to_treat_major}
                  </span>
                </>
              )}
            </p>
          </div>
        )}
      </div>

      <div className="border-t border-line px-4 py-3">
        <p className="text-xs leading-relaxed text-ink-muted">{frax.threshold_basis}</p>

        {frax.contributing_factors.length > 0 && (
          <ul className="mt-3 space-y-1">
            {frax.contributing_factors.slice(0, 4).map((factor) => (
              <li key={factor} className="flex items-start gap-2 text-xs text-ink-faint">
                <span
                  className="mt-[5px] inline-block h-1 w-1 shrink-0"
                  style={{ backgroundColor: elevated ? '#E05C2A' : '#6E7681' }}
                />
                {factor}
              </li>
            ))}
          </ul>
        )}
      </div>

      <footer className="border-t border-line px-4 py-2">
        <p className="text-[11px] leading-relaxed text-ink-faint">
          Treatment thresholds: {FRAX_MAJOR_THRESHOLD}% major, {FRAX_HIP_THRESHOLD}% hip.{' '}
          {frax.calibration_note}
        </p>
      </footer>
    </section>
  )
}

export default RiskScoreBadge
