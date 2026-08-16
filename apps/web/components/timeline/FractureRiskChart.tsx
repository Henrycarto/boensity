'use client'

/**
 * The signature element.
 *
 * A patient's measured T-score history as a solid line, the modelled
 * continuation as a dashed line, and the osteoporosis threshold as a red band
 * across the bottom. When the dashed line enters that band, the header turns
 * burnt coral and states the risk in plain words.
 *
 * That state change is the whole argument. Everything a physician needs to
 * decide whether to treat is in one image: where the patient has been, where
 * they are going, and the line they are going to cross.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import type { ThresholdCrossing, TrajectoryPoint } from '@boensity/shared-types'
import {
  DEFAULT_MARGIN,
  projectionCrossesDangerThreshold,
  renderFractureRiskChart,
} from '@/lib/d3-chart'

interface FractureRiskChartProps {
  points: TrajectoryPoint[]
  crossing?: ThresholdCrossing | null
  currentTScore?: number
  site?: string
  modelQuality?: string
  compact?: boolean
  className?: string
  height?: number
}

function riskHeadline(
  crosses: boolean,
  crossing: ThresholdCrossing | null | undefined,
  currentTScore: number | undefined,
): { label: string; detail: string; elevated: boolean } {
  if (crossing?.already_crossed || (currentTScore !== undefined && currentTScore <= -2.5)) {
    return {
      label: 'Fracture risk: HIGH',
      detail: 'Already below the osteoporosis threshold',
      elevated: true,
    }
  }

  if (crosses && crossing?.years_until_crossing != null) {
    return {
      label: 'Fracture risk: HIGH',
      detail: `Projected to cross in ${crossing.years_until_crossing.toFixed(1)} years`,
      elevated: true,
    }
  }

  if (crosses) {
    return {
      label: 'Fracture risk: HIGH',
      detail: 'Projection enters the osteoporosis range',
      elevated: true,
    }
  }

  return {
    label: 'Fracture risk: MONITORED',
    detail: 'Projection stays above the osteoporosis threshold',
    elevated: false,
  }
}

export function FractureRiskChart({
  points,
  crossing,
  currentTScore,
  site = 'Femoral neck',
  modelQuality,
  compact = false,
  className,
  height = 420,
}: FractureRiskChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const [width, setWidth] = useState(0)

  // The chart is 60 percent of a resizable panel, so width comes from the DOM
  // rather than a prop. Observing the container is the only way to get a
  // correct first paint without a flash at a guessed width.
  useEffect(() => {
    const element = containerRef.current
    if (!element) return

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (entry) setWidth(entry.contentRect.width)
    })

    observer.observe(element)
    setWidth(element.getBoundingClientRect().width)

    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const svg = svgRef.current
    if (!svg || width === 0) return

    renderFractureRiskChart(svg, {
      points,
      crossing,
      compact,
      dimensions: {
        width,
        height,
        margin: compact
          ? { top: 8, right: 12, bottom: 16, left: 24 }
          : DEFAULT_MARGIN,
      },
    })
  }, [points, crossing, width, height, compact])

  const crosses = useMemo(() => projectionCrossesDangerThreshold(points), [points])
  const headline = riskHeadline(crosses, crossing, currentTScore)

  if (compact) {
    return (
      <div ref={containerRef} className={className}>
        <svg ref={svgRef} role="img" aria-label="Bone density trajectory preview" />
      </div>
    )
  }

  return (
    <section className={`panel ${className ?? ''}`}>
      <header
        className="flex items-baseline justify-between border-b px-4 py-3"
        style={{
          borderColor: headline.elevated ? '#E05C2A' : '#2A313C',
          backgroundColor: headline.elevated ? 'rgba(224, 92, 42, 0.08)' : 'transparent',
        }}
      >
        <div className="flex items-baseline gap-3">
          <h2
            className="mono text-sm font-medium tracking-tight"
            style={{ color: headline.elevated ? '#E05C2A' : '#E6EDF3' }}
          >
            {headline.label}
          </h2>
          <span className="text-xs text-ink-muted">{headline.detail}</span>
        </div>
        <div className="flex items-baseline gap-4">
          <span className="label">{site}</span>
          {currentTScore !== undefined && (
            <span className="mono text-sm text-ink">T {currentTScore.toFixed(1)}</span>
          )}
        </div>
      </header>

      <div ref={containerRef} className="px-1 py-2">
        <svg
          ref={svgRef}
          role="img"
          aria-label={`Bone density trajectory at ${site}. ${headline.label}. ${headline.detail}.`}
        />
      </div>

      <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-4 py-2">
        <div className="flex flex-wrap items-center gap-4 text-[11px] text-ink-faint">
          <span className="flex items-center gap-2">
            <svg width="18" height="8" aria-hidden>
              <line x1="0" y1="4" x2="18" y2="4" stroke="#E6EDF3" strokeWidth="2" />
            </svg>
            Measured
          </span>
          <span className="flex items-center gap-2">
            <svg width="18" height="8" aria-hidden>
              <line
                x1="0"
                y1="4"
                x2="18"
                y2="4"
                stroke="#E05C2A"
                strokeWidth="2"
                strokeDasharray="5 4"
              />
            </svg>
            Projected
          </span>
          <span className="flex items-center gap-2">
            <span className="inline-block h-2 w-4" style={{ backgroundColor: 'rgba(224,92,42,0.35)' }} />
            Osteoporosis range
          </span>
        </div>
        {modelQuality && (
          <span className="text-[11px] text-ink-faint">
            Model: {modelQuality.replace(/_/g, ' ')}
          </span>
        )}
      </footer>
    </section>
  )
}

export default FractureRiskChart
