/**
 * D3 rendering core for the fracture risk trajectory chart.
 *
 * Kept out of the React component on purpose. The component owns the DOM node,
 * the resize observer, and the derived header state; this file owns scales,
 * paths, and axes and knows nothing about React. That split is also why the
 * chart is D3 rather than a charting library: what this draws is not a line
 * chart with a threshold annotation bolted on. The threshold band, the point
 * where the projection enters it, and the widening prediction interval are the
 * content. A library that gives you a line and a reference line would need
 * fighting on every one of them.
 *
 * The y axis is inverted relative to intuition: a T-score of 0 is healthy and
 * sits at the top, and the line falls as the patient loses bone. A physician
 * reads a falling line as deterioration without being told, and that reading
 * has to be correct at a glance.
 */

import * as d3 from 'd3'
import type { ThresholdCrossing, TrajectoryPoint } from '@boensity/shared-types'
import { OSTEOPENIA_THRESHOLD, OSTEOPOROSIS_THRESHOLD } from '@boensity/shared-types'

export const CHART_COLORS = {
  base: '#0D1117',
  surface: '#161B22',
  line: '#2A313C',
  ink: '#E6EDF3',
  inkMuted: '#8B949E',
  inkFaint: '#6E7681',
  safe: '#238636',
  warning: '#D29922',
  danger: '#E05C2A',
} as const

export interface ChartDimensions {
  width: number
  height: number
  margin: { top: number; right: number; bottom: number; left: number }
}

export const DEFAULT_MARGIN = { top: 16, right: 68, bottom: 40, left: 48 }

export interface RenderOptions {
  points: TrajectoryPoint[]
  crossing?: ThresholdCrossing | null
  dimensions: ChartDimensions
  showInterval?: boolean
  /** Compact mode drops axis labels and the crossing callout for previews. */
  compact?: boolean
}

interface ParsedPoint extends Omit<TrajectoryPoint, 'date'> {
  date: Date
}

function parsePoints(points: TrajectoryPoint[]): ParsedPoint[] {
  return points
    .map((point) => ({ ...point, date: new Date(point.date) }))
    .filter((point) => !Number.isNaN(point.date.getTime()))
    .sort((a, b) => a.date.getTime() - b.date.getTime())
}

/**
 * Where the projected line enters the osteoporosis band, in chart space.
 *
 * Recomputed here from the plotted points rather than reusing the API's
 * crossing date, so the marker cannot drift away from the line the viewer is
 * actually looking at. The API date is authoritative for the text; this is
 * authoritative for the pixel.
 */
function findCrossingPoint(points: ParsedPoint[]): { date: Date; tScore: number } | null {
  for (let index = 1; index < points.length; index += 1) {
    const previous = points[index - 1]
    const current = points[index]
    if (!previous || !current) continue

    if (previous.t_score > OSTEOPOROSIS_THRESHOLD && current.t_score <= OSTEOPOROSIS_THRESHOLD) {
      const span = current.t_score - previous.t_score
      const fraction = span === 0 ? 0 : (OSTEOPOROSIS_THRESHOLD - previous.t_score) / span
      const time =
        previous.date.getTime() + fraction * (current.date.getTime() - previous.date.getTime())
      return { date: new Date(time), tScore: OSTEOPOROSIS_THRESHOLD }
    }
  }
  return null
}

export function renderFractureRiskChart(
  svgElement: SVGSVGElement,
  options: RenderOptions,
): void {
  const { dimensions, showInterval = true, compact = false } = options
  const { width, height, margin } = dimensions
  const innerWidth = Math.max(0, width - margin.left - margin.right)
  const innerHeight = Math.max(0, height - margin.top - margin.bottom)

  const svg = d3.select(svgElement)
  svg.selectAll('*').remove()

  if (innerWidth <= 0 || innerHeight <= 0) return

  const points = parsePoints(options.points)
  if (points.length === 0) return

  const historical = points.filter((point) => !point.is_projected)
  const projected = points.filter((point) => point.is_projected)

  svg.attr('width', width).attr('height', height).attr('viewBox', `0 0 ${width} ${height}`)

  const root = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`)

  // ---- scales ----
  const xDomain = d3.extent(points, (point) => point.date) as [Date, Date]
  const x = d3.scaleTime().domain(xDomain).range([0, innerWidth])

  // The y domain always includes the osteoporosis threshold plus headroom, so
  // the danger band is on screen even for a patient nowhere near it. A chart
  // that hides the threshold until it becomes relevant gives the physician no
  // sense of how far away the patient is.
  const values = points.flatMap((point) =>
    [point.t_score, point.ci_lower, point.ci_upper].filter(
      (value): value is number => typeof value === 'number',
    ),
  )
  const yMin = Math.min(-3.6, ...values) - 0.3
  const yMax = Math.max(0.6, ...values) + 0.3
  const y = d3.scaleLinear().domain([yMin, yMax]).range([innerHeight, 0]).nice()

  // ---- zone bands ----
  // Painted first so everything else sits on top. Opacity is deliberately low:
  // these are context, and the trajectory has to stay the brightest thing here.
  const zones = root.append('g').attr('class', 'zones')

  const safeTop = y(yMax)
  const safeBottom = y(OSTEOPENIA_THRESHOLD)
  zones
    .append('rect')
    .attr('x', 0)
    .attr('y', safeTop)
    .attr('width', innerWidth)
    .attr('height', Math.max(0, safeBottom - safeTop))
    .attr('fill', CHART_COLORS.safe)
    .attr('opacity', 0.05)

  const warnTop = y(OSTEOPENIA_THRESHOLD)
  const warnBottom = y(OSTEOPOROSIS_THRESHOLD)
  zones
    .append('rect')
    .attr('x', 0)
    .attr('y', warnTop)
    .attr('width', innerWidth)
    .attr('height', Math.max(0, warnBottom - warnTop))
    .attr('fill', CHART_COLORS.warning)
    .attr('opacity', 0.05)

  // The danger band carries roughly three times the warning zone's weight.
  // The two have to be separable at a glance across a room, which is the
  // distance a department head reads this from.
  const dangerTop = y(OSTEOPOROSIS_THRESHOLD)
  zones
    .append('rect')
    .attr('x', 0)
    .attr('y', dangerTop)
    .attr('width', innerWidth)
    .attr('height', Math.max(0, innerHeight - dangerTop))
    .attr('fill', CHART_COLORS.danger)
    .attr('opacity', 0.16)

  // ---- gridlines ----
  root
    .append('g')
    .attr('class', 'grid')
    .selectAll('line')
    .data(y.ticks(6))
    .join('line')
    .attr('x1', 0)
    .attr('x2', innerWidth)
    .attr('y1', (value) => y(value))
    .attr('y2', (value) => y(value))
    .attr('stroke', CHART_COLORS.line)
    .attr('stroke-width', 1)
    .attr('opacity', 0.4)

  // ---- threshold lines ----
  root
    .append('line')
    .attr('x1', 0)
    .attr('x2', innerWidth)
    .attr('y1', y(OSTEOPENIA_THRESHOLD))
    .attr('y2', y(OSTEOPENIA_THRESHOLD))
    .attr('stroke', CHART_COLORS.warning)
    .attr('stroke-width', 1)
    .attr('stroke-dasharray', '2 3')
    .attr('opacity', 0.55)

  // The osteoporosis cutoff. Solid, full weight, brightest line on the chart
  // apart from the trajectory itself. Everything else is context for this.
  root
    .append('line')
    .attr('x1', 0)
    .attr('x2', innerWidth)
    .attr('y1', y(OSTEOPOROSIS_THRESHOLD))
    .attr('y2', y(OSTEOPOROSIS_THRESHOLD))
    .attr('stroke', CHART_COLORS.danger)
    .attr('stroke-width', 2)

  if (!compact) {
    root
      .append('text')
      .attr('x', innerWidth + 8)
      .attr('y', y(OSTEOPOROSIS_THRESHOLD) + 4)
      .attr('fill', CHART_COLORS.danger)
      .attr('font-size', 10)
      .attr('font-family', 'var(--font-dm-mono), monospace')
      .text('-2.5')

    root
      .append('text')
      .attr('x', innerWidth + 8)
      .attr('y', y(OSTEOPENIA_THRESHOLD) + 4)
      .attr('fill', CHART_COLORS.warning)
      .attr('font-size', 10)
      .attr('font-family', 'var(--font-dm-mono), monospace')
      .attr('opacity', 0.8)
      .text('-1.0')
  }

  // ---- prediction interval ----
  // Drawn before the lines so the trajectory reads on top of it. The interval
  // widening as it extends is the visual statement that a projection ten years
  // out is not the same claim as one two years out.
  if (showInterval && projected.length > 0) {
    const withInterval = projected.filter(
      (point) => typeof point.ci_lower === 'number' && typeof point.ci_upper === 'number',
    )

    if (withInterval.length > 1) {
      const area = d3
        .area<ParsedPoint>()
        .x((point) => x(point.date))
        .y0((point) => y(point.ci_lower as number))
        .y1((point) => y(point.ci_upper as number))
        .curve(d3.curveMonotoneX)

      root
        .append('path')
        .datum(withInterval)
        .attr('fill', CHART_COLORS.danger)
        .attr('opacity', 0.1)
        .attr('d', area)
    }
  }

  const lineGenerator = d3
    .line<ParsedPoint>()
    .x((point) => x(point.date))
    .y((point) => y(point.t_score))
    .curve(d3.curveMonotoneX)

  // ---- historical line ----
  if (historical.length > 1) {
    root
      .append('path')
      .datum(historical)
      .attr('fill', 'none')
      .attr('stroke', CHART_COLORS.ink)
      .attr('stroke-width', 2)
      .attr('stroke-linejoin', 'round')
      .attr('stroke-linecap', 'round')
      .attr('d', lineGenerator)
  }

  // ---- projected line ----
  // Starts at the last observed point so the solid and dashed segments join.
  // A visible gap between measured and projected would read as missing data.
  if (projected.length > 0) {
    const lastHistorical = historical[historical.length - 1]
    const projectionPath = lastHistorical ? [lastHistorical, ...projected] : projected

    root
      .append('path')
      .datum(projectionPath)
      .attr('fill', 'none')
      .attr('stroke', CHART_COLORS.danger)
      .attr('stroke-width', 2)
      .attr('stroke-dasharray', '5 4')
      .attr('stroke-linecap', 'round')
      .attr('d', lineGenerator)
  }

  // ---- crossing marker ----
  const crossingPoint = findCrossingPoint(points)
  if (crossingPoint && !compact) {
    const crossingX = x(crossingPoint.date)

    root
      .append('line')
      .attr('x1', crossingX)
      .attr('x2', crossingX)
      .attr('y1', y(crossingPoint.tScore))
      .attr('y2', innerHeight)
      .attr('stroke', CHART_COLORS.danger)
      .attr('stroke-width', 1)
      .attr('stroke-dasharray', '2 2')
      .attr('opacity', 0.7)

    root
      .append('circle')
      .attr('cx', crossingX)
      .attr('cy', y(crossingPoint.tScore))
      .attr('r', 5)
      .attr('fill', CHART_COLORS.base)
      .attr('stroke', CHART_COLORS.danger)
      .attr('stroke-width', 2)

    // Label flips to the left near the right edge so it never runs off.
    const nearRightEdge = crossingX > innerWidth - 90
    root
      .append('text')
      .attr('x', nearRightEdge ? crossingX - 8 : crossingX + 8)
      .attr('y', y(crossingPoint.tScore) - 10)
      .attr('text-anchor', nearRightEdge ? 'end' : 'start')
      .attr('fill', CHART_COLORS.danger)
      .attr('font-size', 10)
      .attr('font-family', 'var(--font-dm-mono), monospace')
      .text(d3.timeFormat('%b %Y')(crossingPoint.date))
  }

  // ---- observed points ----
  const bandColor = (tScore: number): string => {
    if (tScore <= OSTEOPOROSIS_THRESHOLD) return CHART_COLORS.danger
    if (tScore <= OSTEOPENIA_THRESHOLD) return CHART_COLORS.warning
    return CHART_COLORS.safe
  }

  root
    .append('g')
    .selectAll('circle')
    .data(historical)
    .join('circle')
    .attr('cx', (point) => x(point.date))
    .attr('cy', (point) => y(point.t_score))
    .attr('r', compact ? 3 : 4)
    .attr('fill', CHART_COLORS.base)
    .attr('stroke', (point) => bandColor(point.t_score))
    .attr('stroke-width', 2)

  // ---- axes ----
  if (!compact) {
    const xAxis = d3
      .axisBottom(x)
      .ticks(Math.max(3, Math.floor(innerWidth / 90)))
      .tickFormat((value) => d3.timeFormat('%Y')(value as Date))
      .tickSizeOuter(0)

    const xAxisGroup = root
      .append('g')
      .attr('transform', `translate(0,${innerHeight})`)
      .call(xAxis)

    xAxisGroup.selectAll('path, line').attr('stroke', CHART_COLORS.line)
    xAxisGroup
      .selectAll('text')
      .attr('fill', CHART_COLORS.inkFaint)
      .attr('font-size', 10)
      .attr('font-family', 'var(--font-dm-mono), monospace')

    const yAxis = d3.axisLeft(y).ticks(6).tickFormat(d3.format('.1f')).tickSizeOuter(0)
    const yAxisGroup = root.append('g').call(yAxis)

    yAxisGroup.selectAll('path, line').attr('stroke', CHART_COLORS.line)
    yAxisGroup
      .selectAll('text')
      .attr('fill', CHART_COLORS.inkFaint)
      .attr('font-size', 10)
      .attr('font-family', 'var(--font-dm-mono), monospace')

    // A "today" divider, so measured history and projection are separable
    // without reading the dash pattern.
    const lastHistorical = historical[historical.length - 1]
    if (lastHistorical && projected.length > 0) {
      const divider = x(lastHistorical.date)
      root
        .append('line')
        .attr('x1', divider)
        .attr('x2', divider)
        .attr('y1', 0)
        .attr('y2', innerHeight)
        .attr('stroke', CHART_COLORS.inkFaint)
        .attr('stroke-width', 1)
        .attr('opacity', 0.25)

      root
        .append('text')
        .attr('x', divider + 6)
        .attr('y', 11)
        .attr('fill', CHART_COLORS.inkFaint)
        .attr('font-size', 9)
        .attr('letter-spacing', '0.12em')
        .text('LAST SCAN')
    }
  }
}

/** True when any projected point enters the osteoporosis band. */
export function projectionCrossesDangerThreshold(points: TrajectoryPoint[]): boolean {
  return points.some((point) => point.is_projected && point.t_score <= OSTEOPOROSIS_THRESHOLD)
}

export { findCrossingPoint }
