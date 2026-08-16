'use client'

/**
 * Medication adherence, sitting beside the risk score.
 *
 * The placement is the argument. A FRAX number that drops on treatment is only
 * true if the patient takes the treatment, and roughly half of them stop
 * within a year. Showing the projected benefit without showing whether the
 * drug is actually being taken would overstate the case on every screen.
 */

import type { AdherenceResponse, AdherenceBand } from '@boensity/shared-types'
import { Label } from '@/components/ui'
import { cn } from '@/lib/utils'

interface AdherencePanelProps {
  adherence: AdherenceResponse
  liveData?: boolean
  className?: string
}

const BAND_COLOR: Record<AdherenceBand, string> = {
  adherent: '#238636',
  partial: '#D29922',
  non_adherent: '#E05C2A',
  lapsed: '#E05C2A',
}

const BAND_LABEL: Record<AdherenceBand, string> = {
  adherent: 'Adherent',
  partial: 'Partial',
  non_adherent: 'Non-adherent',
  lapsed: 'Lapsed',
}

const DRUG_LABEL: Record<string, string> = {
  oral_bisphosphonate: 'Oral bisphosphonate',
  iv_bisphosphonate: 'IV bisphosphonate',
  denosumab: 'Denosumab',
  serm: 'SERM',
  anabolic: 'Anabolic agent',
  supplement: 'Supplement',
}

export function AdherencePanel({ adherence, liveData, className }: AdherencePanelProps) {
  const { score, interventions } = adherence
  const color = BAND_COLOR[score.band]
  const percent = Math.round(score.proportion_days_covered * 100)

  return (
    <section className={cn('panel', className)}>
      <header className="panel-header">
        <h2 className="label">Medication adherence</h2>
        <div className="flex items-baseline gap-3">
          {liveData === false && <Label>Fixture data</Label>}
          <span
            className="border px-2 py-[2px] text-[10px] uppercase tracking-[0.1em]"
            style={{ borderColor: color, color }}
          >
            {BAND_LABEL[score.band]}
          </span>
        </div>
      </header>

      <div className="px-4 py-5">
        <div className="flex items-start justify-between gap-6">
          <div>
            <div className="mono text-metric-sm" style={{ color }}>
              {percent}
              <span className="text-base">%</span>
            </div>
            <p className="mt-1 text-xs text-ink-muted">Proportion of days covered</p>
          </div>

          <div className="text-right">
            <p className="mono text-sm text-ink">{DRUG_LABEL[score.drug_class] ?? score.drug_class}</p>
            <p className="mono mt-1 text-xs text-ink-faint">
              {score.fill_count} fill{score.fill_count === 1 ? '' : 's'} &middot;{' '}
              {score.days_since_last_fill}d since last
            </p>
          </div>
        </div>

        {/* Coverage bar with the 80 percent threshold marked. The threshold is
            where anti-fracture benefit holds, so the bar has to show the line
            rather than just the value. */}
        <div className="relative mt-5 h-[6px] w-full bg-line">
          <div
            className="absolute left-0 top-0 h-full"
            style={{ width: `${Math.min(100, percent)}%`, backgroundColor: color }}
          />
          <div
            className="absolute top-[-3px] h-[12px] w-[1px]"
            style={{ left: '80%', backgroundColor: '#8B949E' }}
            aria-hidden
          />
        </div>
        <div className="mt-1 flex justify-between text-[10px] text-ink-faint">
          <span>0%</span>
          <span className="mono">80% threshold</span>
          <span>100%</span>
        </div>

        <p className="mt-4 text-xs leading-relaxed text-ink-muted">{score.interpretation}</p>
      </div>

      {/* ---- gaps ---- */}
      {score.gaps.length > 0 && (
        <div className="border-t border-line px-4 py-3">
          <Label>Supply gaps</Label>
          <ul className="mt-2 space-y-1">
            {score.gaps.slice(0, 3).map((gap) => (
              <li key={gap.start} className="mono flex justify-between text-[11px] text-ink-muted">
                <span>
                  {gap.start} to {gap.end}
                </span>
                <span style={{ color: gap.days >= 60 ? '#E05C2A' : '#D29922' }}>{gap.days}d</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* ---- what to do ---- */}
      {interventions.length > 0 && (
        <div className="border-t border-line">
          <div className="px-4 pt-3">
            <Label>Suggested action</Label>
          </div>
          <ul className="px-4 pb-3 pt-2">
            {interventions.map((intervention) => (
              <li key={intervention.channel} className="border-b border-line py-2 last:border-b-0">
                <p className="text-xs text-ink">
                  <span
                    className="mono mr-2 text-[10px] uppercase"
                    style={{
                      color: intervention.urgency === 'high' ? '#E05C2A' : '#D29922',
                    }}
                  >
                    {intervention.channel.replace('_', ' ')}
                  </span>
                  {intervention.message}
                </p>
                <p className="mt-1 text-[11px] leading-relaxed text-ink-faint">
                  {intervention.rationale}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {!score.risk_score_available && (
        <footer className="border-t border-line px-4 py-2">
          <p className="text-[11px] text-ink-faint">
            Composite risk score unavailable. The weighting model is a private component and is
            not part of this deployment. Coverage, gaps, and banding above are unaffected.
          </p>
        </footer>
      )}
    </section>
  )
}

export default AdherencePanel
