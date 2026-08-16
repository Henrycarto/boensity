'use client'

/**
 * Referral routing between a physician and a specialty pharmacy.
 *
 * When the ranking model is not deployed, this presents candidates in clinical
 * eligibility order and says so, rather than showing the first row as though
 * it were a recommendation. The distinction matters: eligibility is a clinical
 * judgement and ranking is a commercial one, and a physician is entitled to
 * know which one they are looking at.
 */

import { useState } from 'react'
import type { ReferralDecision } from '@boensity/shared-types'
import { Button, Label } from '@/components/ui'
import { cn } from '@/lib/utils'

interface PharmacyReferralCardProps {
  referral: ReferralDecision
  onSend?: (pharmacyId: string) => void | Promise<void>
  className?: string
}

export function PharmacyReferralCard({ referral, onSend, className }: PharmacyReferralCardProps) {
  const [selected, setSelected] = useState<string | null>(referral.selected_pharmacy_id)
  const [sending, setSending] = useState(false)

  if (referral.status === 'not_required') {
    return (
      <section className={cn('panel', className)}>
        <header className="panel-header">
          <h2 className="label">Pharmacy referral</h2>
          <Label>Not indicated</Label>
        </header>
        <div className="px-4 py-4">
          <p className="text-xs leading-relaxed text-ink-muted">{referral.reason}</p>
        </div>
      </section>
    )
  }

  const handleSend = async () => {
    if (!selected || !onSend) return
    setSending(true)
    try {
      await onSend(selected)
    } finally {
      setSending(false)
    }
  }

  return (
    <section className={cn('panel', className)}>
      <header
        className="flex items-baseline justify-between border-b px-4 py-3"
        style={{ borderColor: '#D29922', backgroundColor: 'rgba(210, 153, 34, 0.06)' }}
      >
        <h2 className="label" style={{ color: '#D29922' }}>
          Pharmacy referral suggested
        </h2>
        <Label>{referral.candidates.length} eligible</Label>
      </header>

      <div className="border-b border-line px-4 py-3">
        <p className="text-xs leading-relaxed text-ink-muted">{referral.reason}</p>
      </div>

      {referral.candidates.length === 0 ? (
        <div className="px-4 py-4">
          <p className="text-xs text-ink-faint">
            No pharmacy in the connected network can take this referral.
          </p>
        </div>
      ) : (
        <>
          <ul>
            {referral.candidates.map((candidate) => (
              <li key={candidate.pharmacy_id} className="border-b border-line last:border-b-0">
                <label className="flex cursor-pointer items-center justify-between gap-4 px-4 py-3">
                  <span className="flex items-center gap-3">
                    <input
                      type="radio"
                      name="pharmacy"
                      value={candidate.pharmacy_id}
                      checked={selected === candidate.pharmacy_id}
                      onChange={() => setSelected(candidate.pharmacy_id)}
                      className="accent-[#E05C2A]"
                    />
                    <span>
                      <span className="block text-sm text-ink">{candidate.pharmacy_name}</span>
                      <span className="mono block text-[11px] text-ink-faint">
                        {candidate.pharmacy_id}
                        {candidate.distance_km != null && ` · ${candidate.distance_km.toFixed(1)} km`}
                      </span>
                    </span>
                  </span>

                  {candidate.match_score_available && candidate.match_score != null ? (
                    <span className="mono text-xs text-ink-muted">
                      {(candidate.match_score * 100).toFixed(0)}%
                    </span>
                  ) : (
                    <span className="text-[10px] uppercase tracking-[0.1em] text-ink-faint">
                      Eligible
                    </span>
                  )}
                </label>
              </li>
            ))}
          </ul>

          <footer className="flex items-center justify-between gap-4 border-t border-line px-4 py-3">
            <p className="text-[11px] leading-relaxed text-ink-faint">
              {referral.routing_available
                ? 'Ordered by match score.'
                : 'Ordered by clinical eligibility only. The ranking model is a private component and is not part of this deployment, so no pharmacy is preselected.'}
            </p>
            <Button
              variant="primary"
              onClick={handleSend}
              disabled={!selected || sending}
              type="button"
              className="shrink-0"
            >
              {sending ? 'Sending' : 'Send referral'}
            </Button>
          </footer>
        </>
      )}
    </section>
  )
}

export default PharmacyReferralCard
