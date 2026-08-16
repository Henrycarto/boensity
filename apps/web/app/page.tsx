import Link from 'next/link'
import { FractureRiskChart } from '@/components/timeline/FractureRiskChart'
import { getFeaturedPatient, osteoporosisCrossing } from '@/lib/demo-data'
import { formatScanDate } from '@/lib/utils'

/**
 * The landing page opens on a clinical fact and then shows the chart.
 *
 * Nothing precedes that line. No positioning statement, no logo strip, no
 * value proposition. The buyer is a physician who already knows the problem,
 * and the fastest way to lose them is to explain their own specialty to them.
 * The fact, then the evidence, then what it is.
 */
export default function LandingPage() {
  const featured = getFeaturedPatient()
  const crossing = osteoporosisCrossing(featured)
  const trajectory = featured.trajectory

  const lastScan = featured.scans[featured.scans.length - 1]

  return (
    <div className="mx-auto max-w-[1240px] px-6">
      {/* ---- the fact ---- */}
      <section className="border-b border-line py-14">
        <h1 className="max-w-4xl text-2xl font-normal leading-[1.45] tracking-tight text-ink sm:text-[28px]">
          One in three women over 50 will suffer an osteoporotic fracture. Most could be prevented
          with earlier intervention.
        </h1>
      </section>

      {/* ---- the evidence ---- */}
      <section className="py-8">
        <div className="mb-4 flex flex-wrap items-baseline justify-between gap-3">
          <p className="text-sm text-ink-muted">
            {featured.id}, {featured.patient.age.toFixed(0)} year old woman. Five scans over{' '}
            <span className="mono">{trajectory.observation_span_years.toFixed(0)}</span> years.
            Every report she has been given said osteopenia, repeat in two years.
          </p>
          <p className="mono text-xs text-ink-faint">Synthetic patient, real model output</p>
        </div>

        <FractureRiskChart
          points={trajectory.points}
          crossing={crossing}
          currentTScore={trajectory.current_t_score}
          site="Femoral neck"
          modelQuality={trajectory.model_quality}
          height={440}
        />

        {/* The three numbers that make the chart actionable, on one line.
            Not cards. A physician reads a strip of values, not a grid of tiles. */}
        <dl className="mt-px grid grid-cols-2 border border-t-0 border-line sm:grid-cols-4">
          <Metric
            label="Current T-score"
            value={trajectory.current_t_score.toFixed(1)}
            detail={formatScanDate(lastScan?.scan_date ?? trajectory.points[0]?.date ?? '')}
          />
          <Metric
            label="Rate of loss"
            value={`${trajectory.velocity.t_score_per_year.toFixed(3)}/yr`}
            detail={
              trajectory.velocity.exceeds_least_significant_change
                ? 'Above scanner precision error'
                : 'Inside the noise floor'
            }
            accent
          />
          <Metric
            label="Crosses -2.5"
            value={
              crossing?.years_until_crossing != null
                ? `${crossing.years_until_crossing.toFixed(1)} yr`
                : 'Not in window'
            }
            detail={
              crossing?.crossing_date ? formatScanDate(crossing.crossing_date) : 'At current rate'
            }
            accent
          />
          <Metric
            label="10 year FRAX"
            value={`${featured.frax.major_osteoporotic_percent.toFixed(1)}%`}
            detail={
              featured.frax.treatment
                ? `${featured.frax.treatment.major_osteoporotic_percent.toFixed(1)}% on treatment`
                : 'Major osteoporotic'
            }
            accent={featured.frax.intervention_threshold_met}
          />
        </dl>
      </section>

      {/* ---- what it is ---- */}
      <section className="border-t border-line py-12">
        <p className="max-w-3xl text-sm leading-relaxed text-ink-muted">
          A DEXA report tells a physician where a patient is today. It has never told them how fast
          they are moving. Two patients at the same T-score can have completely different futures,
          and static reporting treats them identically. Boensity reads a patient&apos;s scan history
          out of the PDFs it is already scattered across, normalises the values so scans from
          different manufacturers sit on one axis, and models the line between them.
        </p>

        <div className="mt-10 border-t border-line">
          <ServiceRow
            name="Ingest"
            summary="Reads DEXA PDFs from Hologic and GE Lunar consoles, extracts T-scores, BMD, dates, and scanner metadata, and standardises the values onto a single reference so a series that spans a scanner replacement is still a trajectory."
          />
          <ServiceRow
            name="Timeline"
            summary="Stores the longitudinal history, fits the rate of decline against scanner precision error, projects it forward, and computes the ten year fracture probability using the WHO FRAX input variables."
          />
          <ServiceRow
            name="Sync"
            summary="Tracks whether the treatment is actually being taken, because a projected risk reduction only holds if the patient fills the prescription. Flags gaps and routes referrals to pharmacies that can handle the drug."
          />
        </div>
      </section>

      {/* ---- entry points ---- */}
      <section className="flex flex-wrap items-center gap-3 border-t border-line py-10">
        <Link
          href="/patients"
          className="inline-flex items-center justify-center border border-danger bg-danger px-5 py-2 text-sm font-medium text-[#0D1117] transition-colors hover:bg-[#F06A36]"
        >
          Open the demo cohort
        </Link>
        <Link
          href="/ingest"
          className="inline-flex items-center justify-center border border-line-strong px-5 py-2 text-sm font-medium text-ink transition-colors hover:border-ink-muted"
        >
          Upload a scan
        </Link>
        <p className="ml-auto text-[11px] text-ink-faint">
          FRAX figures are an independent implementation of the published methodology, not the
          certified Sheffield tool.
        </p>
      </section>
    </div>
  )
}

function Metric({
  label,
  value,
  detail,
  accent,
}: {
  label: string
  value: string
  detail: string
  accent?: boolean
}) {
  return (
    <div className="border-r border-line px-4 py-4 last:border-r-0">
      <dt className="text-[11px] uppercase tracking-[0.14em] text-ink-faint">{label}</dt>
      <dd
        className="mono mt-2 text-xl tracking-tight"
        style={{ color: accent ? '#E05C2A' : '#E6EDF3' }}
      >
        {value}
      </dd>
      <p className="mt-1 text-[11px] text-ink-faint">{detail}</p>
    </div>
  )
}

function ServiceRow({ name, summary }: { name: string; summary: string }) {
  return (
    <div className="grid gap-2 border-b border-line py-5 sm:grid-cols-[160px_1fr] sm:gap-8">
      <h3 className="text-sm text-ink">Boensity {name}</h3>
      <p className="max-w-2xl text-sm leading-relaxed text-ink-muted">{summary}</p>
    </div>
  )
}
