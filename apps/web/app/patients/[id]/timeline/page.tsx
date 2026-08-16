import Link from 'next/link'
import { notFound } from 'next/navigation'
import { FractureRiskChart } from '@/components/timeline/FractureRiskChart'
import { RiskScoreBadge } from '@/components/timeline/RiskScoreBadge'
import { AdherencePanel } from '@/components/sync/AdherencePanel'
import { PharmacyReferralCard } from '@/components/sync/PharmacyReferralCard'
import { ManufacturerBadge } from '@/components/ingest/ManufacturerBadge'
import { AlertRow, Label, TScoreValue } from '@/components/ui'
import { getDemoAdherence, getDemoPatient, osteoporosisCrossing } from '@/lib/demo-data'
import { formatScanDate } from '@/lib/utils'
import { SITE_LABEL } from '@boensity/shared-types'

/**
 * The core screen.
 *
 * Scan history down the left, the trajectory chart taking the majority of the
 * width on the right, and the two panels that turn it into a decision sitting
 * directly underneath: what the ten year risk is, and whether the patient is
 * taking anything about it.
 *
 * The order is the clinical reasoning order. What was measured, what it
 * implies, what to do.
 */

export default function PatientTimelinePage({ params }: { params: { id: string } }) {
  const patient = getDemoPatient(params.id)
  if (!patient) notFound()

  const { trajectory, frax, scans } = patient
  const crossing = osteoporosisCrossing(patient)
  const adherence = getDemoAdherence(patient.id)

  // Newest first. A clinician opening a chart looks at the most recent scan.
  const history = [...scans].reverse()

  return (
    <div className="mx-auto max-w-[1600px] px-6 py-6">
      {/* ---- patient header ---- */}
      <header className="mb-6 flex flex-wrap items-baseline justify-between gap-4 border-b border-line pb-4">
        <div className="flex items-baseline gap-6">
          <h1 className="mono text-lg tracking-tight text-ink">{patient.id}</h1>
          <p className="text-sm text-ink-muted">
            {patient.patient.age.toFixed(0)} year old{' '}
            {patient.patient.sex === 'female' ? 'woman' : 'man'} &middot;{' '}
            {SITE_LABEL[trajectory.site]}
          </p>
        </div>

        <div className="flex items-baseline gap-6">
          <span className="text-xs text-ink-faint">
            <span className="mono text-ink-muted">{trajectory.scan_count}</span> scans over{' '}
            <span className="mono text-ink-muted">
              {trajectory.observation_span_years.toFixed(0)}
            </span>{' '}
            years
          </span>
          <Link href={`/patients/${patient.id}`} className="text-xs text-ink-muted hover:text-ink">
            Patient record
          </Link>
        </div>
      </header>

      {/* ---- scan history left, chart right ---- */}
      <div className="grid gap-px lg:grid-cols-[minmax(280px,1fr)_2.2fr]">
        <section className="panel">
          <div className="panel-header">
            <h2 className="label">Scan history</h2>
            <Label>{trajectory.scan_count}</Label>
          </div>

          <ol>
            {history.map((scan, index) => {
              const previous = history[index + 1]
              const delta = previous ? scan.t_score - previous.t_score : null

              return (
                <li key={`${scan.scan_date}-${scan.site}`} className="border-b border-line last:border-b-0">
                  <div className="px-4 py-3">
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="mono text-xs text-ink-muted">
                        {formatScanDate(scan.scan_date)}
                      </span>
                      <TScoreValue value={scan.t_score} />
                    </div>

                    <div className="mt-2 flex items-center justify-between gap-3">
                      <ManufacturerBadge
                        manufacturer={scan.manufacturer}
                        scannerModel={scan.scanner_model}
                      />
                      {/* Change since the previous scan. The single most useful
                          number in a list of scans and the one no DEXA report
                          prints. */}
                      {delta !== null && (
                        <span
                          className="mono text-[11px]"
                          style={{ color: delta < 0 ? '#E05C2A' : '#238636' }}
                        >
                          {delta > 0 ? '+' : ''}
                          {delta.toFixed(2)}
                        </span>
                      )}
                    </div>

                    {scan.bmd_g_cm2 != null && (
                      <p className="mono mt-2 text-[11px] text-ink-faint">
                        BMD {scan.bmd_g_cm2.toFixed(3)} g/cm2
                      </p>
                    )}
                  </div>
                </li>
              )
            })}
          </ol>

          <div className="border-t border-line px-4 py-3">
            <Label>Rate of change</Label>
            <p
              className="mono mt-2 text-lg"
              style={{
                color:
                  trajectory.velocity.direction === 'declining'
                    ? '#E05C2A'
                    : trajectory.velocity.direction === 'improving'
                      ? '#238636'
                      : '#8B949E',
              }}
            >
              {trajectory.velocity.t_score_per_year > 0 ? '+' : ''}
              {trajectory.velocity.t_score_per_year.toFixed(3)}
              <span className="text-xs text-ink-faint"> /yr</span>
            </p>

            {trajectory.velocity.bmd_percent_per_year != null && (
              <p className="mono mt-1 text-[11px] text-ink-faint">
                {trajectory.velocity.bmd_percent_per_year > 0 ? '+' : ''}
                {trajectory.velocity.bmd_percent_per_year.toFixed(2)}% BMD/yr
              </p>
            )}

            <p className="mt-3 text-[11px] leading-relaxed text-ink-muted">
              {trajectory.velocity.interpretation}
            </p>

            {trajectory.velocity.ci_lower != null && trajectory.velocity.ci_upper != null && (
              <p className="mono mt-2 text-[11px] text-ink-faint">
                95% CI {trajectory.velocity.ci_lower.toFixed(3)} to{' '}
                {trajectory.velocity.ci_upper.toFixed(3)}
              </p>
            )}
          </div>
        </section>

        <FractureRiskChart
          points={trajectory.points}
          crossing={crossing}
          currentTScore={trajectory.current_t_score}
          site={SITE_LABEL[trajectory.site]}
          modelQuality={trajectory.model_quality}
          height={480}
        />
      </div>

      {/* ---- risk score and adherence, side by side ---- */}
      <div className="mt-px grid gap-px lg:grid-cols-2">
        <RiskScoreBadge frax={frax} />
        {adherence ? (
          <AdherencePanel adherence={adherence} liveData={false} />
        ) : (
          <section className="panel">
            <div className="panel-header">
              <h2 className="label">Medication adherence</h2>
            </div>
            <div className="px-4 py-6">
              <p className="text-xs text-ink-faint">
                No pharmacy record connected for this patient.
              </p>
            </div>
          </section>
        )}
      </div>

      {/* ---- alerts and referral ---- */}
      <div className="mt-px grid gap-px lg:grid-cols-2">
        <section className="panel">
          <div className="panel-header">
            <h2 className="label">Clinical alerts</h2>
            <Label>{trajectory.alerts.length}</Label>
          </div>
          {trajectory.alerts.length === 0 ? (
            <p className="px-4 py-6 text-xs text-ink-faint">Nothing flagged for this patient.</p>
          ) : (
            <ul>
              {trajectory.alerts.map((alert) => (
                <AlertRow
                  key={alert.code}
                  severity={alert.severity}
                  title={alert.title}
                  detail={alert.detail}
                />
              ))}
            </ul>
          )}
        </section>

        {adherence?.referral ? (
          <PharmacyReferralCard referral={adherence.referral} />
        ) : (
          <section className="panel">
            <div className="panel-header">
              <h2 className="label">Pharmacy referral</h2>
            </div>
            <p className="px-4 py-6 text-xs text-ink-faint">
              Referral routing requires a connected pharmacy network.
            </p>
          </section>
        )}
      </div>
    </div>
  )
}

export function generateStaticParams() {
  return ['BN-4471', 'BN-2298', 'BN-8810', 'BN-6034', 'BN-1157'].map((id) => ({ id }))
}
