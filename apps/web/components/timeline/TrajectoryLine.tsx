'use client'

/**
 * Compact trajectory sparkline for list rows.
 *
 * The patient list has to answer "who is falling toward the line" across a
 * whole department at a glance. A T-score column alone cannot: the patient at
 * -2.3 who has been flat for six years is fine, and the one at -1.6 dropping
 * fast is not. Both need the shape next to the number.
 *
 * Pure SVG rather than D3. There is no axis, no interaction, and one path, so
 * pulling D3 into every row of a long list would cost more than the arithmetic
 * it saves.
 */

import { useMemo } from 'react'
import type { TrajectoryPoint } from '@boensity/shared-types'
import { OSTEOPOROSIS_THRESHOLD } from '@boensity/shared-types'
import { DangerThresholdMarker } from './DangerThresholdMarker'

interface TrajectoryLineProps {
  points: TrajectoryPoint[]
  width?: number
  height?: number
  showThreshold?: boolean
  className?: string
}

export function TrajectoryLine({
  points,
  width = 120,
  height = 36,
  showThreshold = true,
  className,
}: TrajectoryLineProps) {
  const geometry = useMemo(() => {
    const parsed = points
      .map((point) => ({ ...point, time: new Date(point.date).getTime() }))
      .filter((point) => !Number.isNaN(point.time))
      .sort((a, b) => a.time - b.time)

    if (parsed.length < 2) return null

    const times = parsed.map((point) => point.time)
    const minTime = Math.min(...times)
    const maxTime = Math.max(...times)
    const timeSpan = maxTime - minTime || 1

    // The domain is pinned wide enough to always contain the threshold, so
    // the same T-score sits at the same height in every row of the list.
    const scores = parsed.map((point) => point.t_score)
    const minScore = Math.min(-3.2, ...scores)
    const maxScore = Math.max(0.4, ...scores)
    const scoreSpan = maxScore - minScore || 1

    const x = (time: number) => ((time - minTime) / timeSpan) * width
    const y = (score: number) => height - ((score - minScore) / scoreSpan) * height

    const toPath = (subset: typeof parsed) =>
      subset.map((point, index) => `${index === 0 ? 'M' : 'L'}${x(point.time)},${y(point.t_score)}`).join(' ')

    const historical = parsed.filter((point) => !point.is_projected)
    const projected = parsed.filter((point) => point.is_projected)
    const lastHistorical = historical[historical.length - 1]

    return {
      historicalPath: historical.length > 1 ? toPath(historical) : null,
      projectedPath:
        projected.length > 0
          ? toPath(lastHistorical ? [lastHistorical, ...projected] : projected)
          : null,
      y,
      endsInDanger: (parsed[parsed.length - 1]?.t_score ?? 0) <= OSTEOPOROSIS_THRESHOLD,
    }
  }, [points, width, height])

  if (!geometry) {
    return (
      <div
        className={className}
        style={{ width, height }}
        aria-label="Not enough scans to draw a trajectory"
      />
    )
  }

  return (
    <svg
      className={className}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={
        geometry.endsInDanger
          ? 'Trajectory projected into the osteoporosis range'
          : 'Trajectory above the osteoporosis threshold'
      }
    >
      {showThreshold && (
        <DangerThresholdMarker yScale={geometry.y} width={width} height={height} />
      )}

      {geometry.historicalPath && (
        <path
          d={geometry.historicalPath}
          fill="none"
          stroke="#E6EDF3"
          strokeWidth={1.5}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      )}

      {geometry.projectedPath && (
        <path
          d={geometry.projectedPath}
          fill="none"
          stroke="#E05C2A"
          strokeWidth={1.5}
          strokeDasharray="3 3"
          strokeLinecap="round"
        />
      )}
    </svg>
  )
}

export default TrajectoryLine
