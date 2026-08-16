'use client'

/**
 * The osteoporosis threshold band, as a declarative SVG fragment.
 *
 * The main chart draws its own band through D3 because it also needs axes,
 * intervals, and a crossing marker. This component covers everywhere else the
 * threshold has to appear: the sparkline in a patient list row, a preview
 * tile, a printed summary. Those places need the same line at the same value
 * in the same colour, and duplicating the geometry by hand in each of them is
 * how a threshold ends up drawn at -2.0 in one view.
 */

import { OSTEOPOROSIS_THRESHOLD, OSTEOPENIA_THRESHOLD } from '@boensity/shared-types'

interface DangerThresholdMarkerProps {
  /** Maps a T-score to a y pixel in the parent SVG's coordinate space. */
  yScale: (tScore: number) => number
  width: number
  height: number
  showOsteopenia?: boolean
  showLabel?: boolean
}

export function DangerThresholdMarker({
  yScale,
  width,
  height,
  showOsteopenia = false,
  showLabel = false,
}: DangerThresholdMarkerProps) {
  const dangerY = yScale(OSTEOPOROSIS_THRESHOLD)
  const bandHeight = Math.max(0, height - dangerY)

  return (
    <g aria-hidden>
      <rect x={0} y={dangerY} width={width} height={bandHeight} fill="#E05C2A" opacity={0.12} />

      {showOsteopenia && (
        <line
          x1={0}
          x2={width}
          y1={yScale(OSTEOPENIA_THRESHOLD)}
          y2={yScale(OSTEOPENIA_THRESHOLD)}
          stroke="#D29922"
          strokeWidth={1}
          strokeDasharray="2 3"
          opacity={0.55}
        />
      )}

      <line x1={0} x2={width} y1={dangerY} y2={dangerY} stroke="#E05C2A" strokeWidth={1.5} />

      {showLabel && (
        <text
          x={width - 4}
          y={dangerY - 4}
          textAnchor="end"
          fill="#E05C2A"
          fontSize={9}
          fontFamily="var(--font-dm-mono), monospace"
        >
          {OSTEOPOROSIS_THRESHOLD.toFixed(1)}
        </text>
      )}
    </g>
  )
}

export default DangerThresholdMarker
