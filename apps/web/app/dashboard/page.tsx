import Link from 'next/link'
import { TrajectoryLine } from '@/components/timeline/TrajectoryLine'
import { Label, TScoreValue } from '@/components/ui'
import { DEMO_PATIENTS, getDemoAdherence, osteoporosisCrossing } from '@/lib/demo-data'

/**
 * Department view.
 *
 * Answers one question: who in this cohort needs attention that they are not
 * currently getting. Everything on it is a queue rather than a statistic,
 * because a dashboard that reports totals without naming patients generates
 * no clinical action.
 */
export default function DashboardPage() {
  const patients = DEMO_PATIENTS

  const crossingSoon = patients
    .map((patient) => ({ patient, crossing: osteoporosisCrossing(patient) }))
    .filter(
      ({ crossing }) =>
        crossing?.crossing_date != null && (crossing.years_until_crossing ?? 99) <= 10,
    )
    .sort(
      (a, b) => (a.crossing?.years_until_crossing ?? 99) - (b.crossing?.years_until_crossing ?? 99),
    )

  const aboveThreshold = patients.filter((patient) => patient.frax.intervention_threshold_met)

  const adherenceProblems = patients
    .map((patient) => ({ patient, adherence: getDemoAdherence(patient.id) }))
    .filter(
      ({ adherence }) =>
        adherence && (adherence.score.band === 'lapsed' || adherence.score.band === 'non_adherent'),
    )

  const alreadyOsteoporotic = patients.filter(
    (patient) => patient.trajectory.current_band === 'osteoporosis',
  )

  return (
    <div className="mx-auto max-w-[1600px] px-6 py-6">
      <header className="mb-6 border-b border-line pb-4">
        <h1 className="text-lg tracking-tight text-ink">Department overview</h1>
        <p className="mt-1 text-xs text-ink-muted">
          Bone health unit. Every row is a patient whose current management may not match their
          trajectory.
        </p>
      </header>

      {/* Counters as a single strip. Not cards, not a grid of tiles. */}
      <dl className="mb-px grid grid-cols-2 border border-line sm:grid-cols-4">
        <Counter label="In cohort" value={patients.length} />
        <Counter label="Already osteoporotic" value={alreadyOsteoporotic.length} accent />
        <Counter label="Above FRAX threshold" value={aboveThreshold.length} accent />
        <Counter label="Adherence problem" value={adherenceProblems.length} accent />
      </dl>

      <div className="grid gap-px lg:grid-cols-2">
        {/* ---- projected crossings ---- */}
        <section className="panel">
          <div className="panel-header">
            <h2 className="label">Projected to cross the threshold</h2>
            <Label>{crossingSoon.length}</Label>
          </div>

          {crossingSoon.length === 0 ? (
            <p className="px-4 py-6 text-xs text-ink-faint">
              No patient in the cohort is projected to cross inside ten years.
            </p>
          ) : (
            <ul>
              {crossingSoon.map(({ patient, crossing }) => (
                <li key={patient.id} className="border-b border-line last:border-b-0">
                  <Link
                    href={`/patients/${patient.id}/timeline`}
                    className="flex items-center justify-between gap-4 px-4 py-3 hover:bg-surface-raised"
                  >
                    <div className="min-w-0">
                      <span className="mono text-sm text-ink">{patient.id}</span>
                      <p className="mt-1 text-[11px] text-ink-faint">
                        {patient.trajectory.velocity.t_score_per_year.toFixed(3)} per year,{' '}
                        {crossing?.confidence} confidence
                      </p>
                    </div>

                    <TrajectoryLine points={patient.trajectory.points} width={90} height={28} />

                    <div className="shrink-0 text-right">
                      <span className="mono text-sm" style={{ color: '#E05C2A' }}>
                        {crossing?.years_until_crossing?.toFixed(1)} yr
                      </span>
                      <p className="mt-1 text-[11px] text-ink-faint">to -2.5</p>
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* ---- above FRAX intervention threshold ---- */}
        <section className="panel">
          <div className="panel-header">
            <h2 className="label">At or above the FRAX treatment threshold</h2>
            <Label>{aboveThreshold.length}</Label>
          </div>

          {aboveThreshold.length === 0 ? (
            <p className="px-4 py-6 text-xs text-ink-faint">No patient meets the threshold.</p>
          ) : (
            <ul>
              {aboveThreshold.map((patient) => (
                <li key={patient.id} className="border-b border-line last:border-b-0">
                  <Link
                    href={`/patients/${patient.id}/timeline`}
                    className="flex items-center justify-between gap-4 px-4 py-3 hover:bg-surface-raised"
                  >
                    <div className="min-w-0">
                      <span className="mono text-sm text-ink">{patient.id}</span>
                      <p className="mt-1 text-[11px] text-ink-faint">
                        {patient.frax.treatment
                          ? `Drops to ${patient.frax.treatment.major_osteoporotic_percent.toFixed(1)}% on treatment`
                          : 'Major osteoporotic probability'}
                      </p>
                    </div>

                    <div className="flex items-baseline gap-4">
                      <TScoreValue value={patient.trajectory.current_t_score} />
                      <span className="mono text-sm" style={{ color: '#E05C2A' }}>
                        {patient.frax.major_osteoporotic_percent.toFixed(1)}%
                      </span>
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {/* ---- adherence ---- */}
      <section className="panel mt-px">
        <div className="panel-header">
          <h2 className="label">Treatment not being taken</h2>
          <Label>{adherenceProblems.length}</Label>
        </div>

        {adherenceProblems.length === 0 ? (
          <p className="px-4 py-6 text-xs text-ink-faint">
            No adherence problems in the connected pharmacy records.
          </p>
        ) : (
          <ul>
            {adherenceProblems.map(({ patient, adherence }) => (
              <li key={patient.id} className="border-b border-line last:border-b-0">
                <Link
                  href={`/patients/${patient.id}/timeline`}
                  className="flex flex-wrap items-center justify-between gap-4 px-4 py-3 hover:bg-surface-raised"
                >
                  <div className="min-w-0">
                    <span className="mono text-sm text-ink">{patient.id}</span>
                    <p className="mt-1 max-w-xl text-[11px] leading-relaxed text-ink-faint">
                      {adherence?.score.interpretation}
                    </p>
                  </div>

                  <div className="shrink-0 text-right">
                    <span className="mono text-sm" style={{ color: '#E05C2A' }}>
                      {Math.round((adherence?.score.proportion_days_covered ?? 0) * 100)}%
                    </span>
                    <p className="mt-1 text-[11px] text-ink-faint">days covered</p>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

function Counter({ label, value, accent }: { label: string; value: number; accent?: boolean }) {
  return (
    <div className="border-r border-line px-4 py-4 last:border-r-0">
      <dt className="text-[11px] uppercase tracking-[0.14em] text-ink-faint">{label}</dt>
      <dd
        className="mono mt-2 text-2xl tracking-tight"
        style={{ color: accent && value > 0 ? '#E05C2A' : '#E6EDF3' }}
      >
        {value}
      </dd>
    </div>
  )
}
