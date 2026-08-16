import Link from 'next/link'
import { TrajectoryLine } from '@/components/timeline/TrajectoryLine'
import { TScoreValue, Label } from '@/components/ui'
import { DEMO_PATIENTS, osteoporosisCrossing } from '@/lib/demo-data'
import { formatScanDate } from '@/lib/utils'

/**
 * The cohort screen.
 *
 * Sorted by rate of loss, not by current T-score. That ordering is the whole
 * point: the patients worth finding are the ones who still look acceptable
 * today and will not in three years. Sorting by T-score puts them in the
 * middle of the list where nobody looks.
 */
export default function PatientsPage() {
  const patients = [...DEMO_PATIENTS].sort(
    (a, b) => a.trajectory.velocity.t_score_per_year - b.trajectory.velocity.t_score_per_year,
  )

  return (
    <div className="mx-auto max-w-[1600px] px-6 py-6">
      <header className="mb-6 flex flex-wrap items-baseline justify-between gap-4 border-b border-line pb-4">
        <div>
          <h1 className="text-lg tracking-tight text-ink">Patients</h1>
          <p className="mt-1 text-xs text-ink-muted">
            Ordered by rate of bone loss. The fastest decline is at the top regardless of where
            the patient currently sits.
          </p>
        </div>
        <Label>{patients.length} in cohort</Label>
      </header>

      <div className="panel overflow-x-auto">
        <table className="w-full min-w-[880px] text-sm">
          <thead>
            <tr className="border-b border-line text-left">
              <Th>Patient</Th>
              <Th>Trajectory</Th>
              <Th align="right">Current T</Th>
              <Th align="right">Rate</Th>
              <Th align="right">Crosses -2.5</Th>
              <Th align="right">10 yr FRAX</Th>
              <Th>Last scan</Th>
            </tr>
          </thead>
          <tbody>
            {patients.map((patient) => {
              const crossing = osteoporosisCrossing(patient)
              const velocity = patient.trajectory.velocity
              const lastScan = patient.scans[patient.scans.length - 1]

              const crossingLabel = crossing?.already_crossed
                ? 'Already'
                : crossing?.years_until_crossing != null && crossing.crossing_date
                  ? `${crossing.years_until_crossing.toFixed(1)} yr`
                  : 'Not in window'

              const crossingUrgent =
                crossing?.already_crossed ||
                (crossing?.crossing_date != null &&
                  (crossing.years_until_crossing ?? 99) <= 5)

              return (
                <tr key={patient.id} className="border-b border-line last:border-b-0 hover:bg-surface-raised">
                  <td className="px-4 py-3">
                    <Link href={`/patients/${patient.id}/timeline`} className="block">
                      <span className="mono text-sm text-ink">{patient.id}</span>
                      <span className="mt-1 block text-[11px] text-ink-faint">{patient.note}</span>
                    </Link>
                  </td>

                  <td className="px-4 py-3">
                    <TrajectoryLine points={patient.trajectory.points} width={130} height={34} />
                  </td>

                  <td className="px-4 py-3 text-right">
                    <TScoreValue value={patient.trajectory.current_t_score} />
                  </td>

                  <td className="mono px-4 py-3 text-right text-xs">
                    <span
                      style={{
                        color:
                          velocity.direction === 'declining'
                            ? '#E05C2A'
                            : velocity.direction === 'improving'
                              ? '#238636'
                              : '#8B949E',
                      }}
                    >
                      {velocity.t_score_per_year > 0 ? '+' : ''}
                      {velocity.t_score_per_year.toFixed(3)}
                    </span>
                    {!velocity.exceeds_least_significant_change && (
                      <span className="ml-2 text-[10px] text-ink-faint">noise</span>
                    )}
                  </td>

                  <td
                    className="mono px-4 py-3 text-right text-xs"
                    style={{ color: crossingUrgent ? '#E05C2A' : '#8B949E' }}
                  >
                    {crossingLabel}
                  </td>

                  <td className="mono px-4 py-3 text-right text-xs">
                    <span
                      style={{
                        color: patient.frax.intervention_threshold_met ? '#E05C2A' : '#E6EDF3',
                      }}
                    >
                      {patient.frax.major_osteoporotic_percent.toFixed(1)}%
                    </span>
                  </td>

                  <td className="mono px-4 py-3 text-xs text-ink-faint">
                    {lastScan ? formatScanDate(lastScan.scan_date) : 'n/a'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <p className="mt-4 text-[11px] text-ink-faint">
        Synthetic patients. Every value is computed by the Timeline engine from the scan series
        shown on each patient page.
      </p>
    </div>
  )
}

function Th({ children, align = 'left' }: { children: React.ReactNode; align?: 'left' | 'right' }) {
  return (
    <th
      className={`px-4 py-2 text-[11px] font-normal uppercase tracking-[0.14em] text-ink-faint ${
        align === 'right' ? 'text-right' : ''
      }`}
    >
      {children}
    </th>
  )
}
