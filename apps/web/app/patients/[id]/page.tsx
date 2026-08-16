import Link from 'next/link'
import { notFound } from 'next/navigation'
import { TrajectoryLine } from '@/components/timeline/TrajectoryLine'
import { ManufacturerBadge } from '@/components/ingest/ManufacturerBadge'
import { BandPill, Label, TScoreValue } from '@/components/ui'
import { getDemoPatient, osteoporosisCrossing } from '@/lib/demo-data'
import { formatScanDate } from '@/lib/utils'
import { SITE_LABEL } from '@boensity/shared-types'

/**
 * Patient record: the inputs behind the trajectory.
 *
 * Separate from the timeline screen because the two answer different
 * questions. The timeline answers "what is happening to this patient". This
 * answers "what exactly is that conclusion built on", which is the screen a
 * physician opens when they want to check the machine's work.
 */
export default function PatientRecordPage({ params }: { params: { id: string } }) {
  const patient = getDemoPatient(params.id)
  if (!patient) notFound()

  const { trajectory, frax } = patient
  const crossing = osteoporosisCrossing(patient)
  const context = patient.patient
  const bmi = context.weight_kg / Math.pow(context.height_cm / 100, 2)

  const riskFactors: Array<[string, boolean]> = [
    ['Previous fragility fracture', context.previous_fracture],
    ['Parent hip fracture', context.parent_hip_fracture],
    ['Current smoker', context.current_smoking],
    ['Glucocorticoid exposure', context.glucocorticoids],
    ['Rheumatoid arthritis', context.rheumatoid_arthritis],
    ['Secondary osteoporosis', context.secondary_osteoporosis],
    ['Alcohol 3 or more units daily', context.alcohol_three_or_more_units],
  ]

  return (
    <div className="mx-auto max-w-[1240px] px-6 py-6">
      <header className="mb-6 flex flex-wrap items-baseline justify-between gap-4 border-b border-line pb-4">
        <div className="flex items-baseline gap-6">
          <h1 className="mono text-lg tracking-tight text-ink">{patient.id}</h1>
          <BandPill band={trajectory.current_band} />
        </div>
        <Link
          href={`/patients/${patient.id}/timeline`}
          className="inline-flex items-center border border-danger bg-danger px-4 py-2 text-sm font-medium text-[#0D1117] transition-colors hover:bg-[#F06A36]"
        >
          Open trajectory
        </Link>
      </header>

      <div className="grid gap-px lg:grid-cols-[1fr_1fr]">
        {/* ---- demographics and FRAX inputs ---- */}
        <section className="panel">
          <div className="panel-header">
            <h2 className="label">FRAX input variables</h2>
            <Label>{frax.bmd_included ? 'With BMD' : 'Without BMD'}</Label>
          </div>

          <dl className="grid grid-cols-2 gap-x-6 gap-y-4 px-4 py-4">
            <Field label="Age" value={`${context.age.toFixed(0)} years`} />
            <Field label="Sex" value={context.sex === 'female' ? 'Female' : 'Male'} />
            <Field label="Weight" value={`${context.weight_kg.toFixed(0)} kg`} />
            <Field label="Height" value={`${context.height_cm.toFixed(0)} cm`} />
            <Field label="BMI" value={bmi.toFixed(1)} />
            <Field
              label="Femoral neck T"
              value={trajectory.current_t_score.toFixed(1)}
              accent={trajectory.current_t_score <= -2.5}
            />
          </dl>

          <div className="border-t border-line px-4 py-4">
            <Label>Clinical risk factors</Label>
            <ul className="mt-3 space-y-2">
              {riskFactors.map(([label, present]) => (
                <li key={label} className="flex items-center justify-between gap-3 text-xs">
                  <span className={present ? 'text-ink' : 'text-ink-faint'}>{label}</span>
                  <span
                    className="mono text-[11px]"
                    style={{ color: present ? '#E05C2A' : '#6E7681' }}
                  >
                    {present ? 'yes' : 'no'}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </section>

        {/* ---- derived summary ---- */}
        <section className="panel">
          <div className="panel-header">
            <h2 className="label">Model output</h2>
            <Label>{trajectory.model_quality.replace(/_/g, ' ')}</Label>
          </div>

          <div className="px-4 py-4">
            <TrajectoryLine points={trajectory.points} width={260} height={60} />
          </div>

          <dl className="grid grid-cols-2 gap-x-6 gap-y-4 border-t border-line px-4 py-4">
            <Field
              label="Rate of loss"
              value={`${trajectory.velocity.t_score_per_year.toFixed(3)}/yr`}
              accent={trajectory.velocity.direction === 'declining'}
            />
            <Field
              label="Scans"
              value={`${trajectory.scan_count} over ${trajectory.observation_span_years.toFixed(0)} yr`}
            />
            <Field
              label="Crosses -2.5"
              value={
                crossing?.already_crossed
                  ? 'Already crossed'
                  : crossing?.crossing_date
                    ? formatScanDate(crossing.crossing_date)
                    : 'Not in window'
              }
              accent={Boolean(crossing?.already_crossed || crossing?.crossing_date)}
            />
            <Field
              label="Projection confidence"
              value={crossing?.confidence ?? 'not applicable'}
            />
            <Field
              label="10 yr major osteoporotic"
              value={`${frax.major_osteoporotic_percent.toFixed(1)}%`}
              accent={frax.intervention_threshold_met}
            />
            <Field label="10 yr hip" value={`${frax.hip_percent.toFixed(1)}%`} />
          </dl>

          <div className="border-t border-line px-4 py-4">
            <p className="text-xs leading-relaxed text-ink-muted">{frax.threshold_basis}</p>
          </div>
        </section>
      </div>

      {/* ---- source scans ---- */}
      <section className="panel mt-px">
        <div className="panel-header">
          <h2 className="label">Source scans</h2>
          <Label>{patient.scans.length}</Label>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-sm">
            <thead>
              <tr className="border-b border-line text-left">
                <Th>Date</Th>
                <Th>Site</Th>
                <Th>Scanner</Th>
                <Th align="right">BMD</Th>
                <Th align="right">T-score</Th>
              </tr>
            </thead>
            <tbody>
              {[...patient.scans].reverse().map((scan) => (
                <tr key={`${scan.scan_date}-${scan.site}`} className="border-b border-line last:border-b-0">
                  <td className="mono px-4 py-3 text-xs text-ink-muted">
                    {formatScanDate(scan.scan_date)}
                  </td>
                  <td className="px-4 py-3 text-xs text-ink-muted">{SITE_LABEL[scan.site]}</td>
                  <td className="px-4 py-3">
                    <ManufacturerBadge
                      manufacturer={scan.manufacturer}
                      scannerModel={scan.scanner_model}
                    />
                  </td>
                  <td className="mono px-4 py-3 text-right text-xs text-ink-muted">
                    {scan.bmd_g_cm2?.toFixed(3) ?? 'n/a'}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <TScoreValue value={scan.t_score} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}

function Field({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-[0.14em] text-ink-faint">{label}</dt>
      <dd className="mono mt-1 text-sm" style={{ color: accent ? '#E05C2A' : '#E6EDF3' }}>
        {value}
      </dd>
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

export function generateStaticParams() {
  return ['BN-4471', 'BN-2298', 'BN-8810', 'BN-6034', 'BN-1157'].map((id) => ({ id }))
}
