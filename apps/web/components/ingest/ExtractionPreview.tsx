'use client'

/**
 * Review screen for an extracted scan.
 *
 * The confirmation gate. Nothing reaches a patient's timeline until a human
 * has seen what the extractor read, and every value here is editable because
 * the whole point of review is that the machine can be wrong.
 *
 * Both T-scores are shown side by side: the number printed on the report and
 * the number recomputed from standardised BMD. A physician comparing this
 * screen against the PDF in their other hand needs to find the printed value,
 * and the trajectory needs the standardised one. Hiding either would break one
 * of those two jobs.
 */

import { useState } from 'react'
import type { ExtractionPreviewPayload, NormalizedMeasurement } from '@boensity/shared-types'
import { SITE_LABEL } from '@boensity/shared-types'
import { Button, ConfidenceBar, Label, TScoreValue } from '@/components/ui'
import { ManufacturerBadge } from './ManufacturerBadge'
import { cn, formatScanDate } from '@/lib/utils'

interface ExtractionPreviewProps {
  payload: ExtractionPreviewPayload
  onConfirm?: (scan: ExtractionPreviewPayload['scan']) => void | Promise<void>
  onDiscard?: () => void
  className?: string
}

export function ExtractionPreview({
  payload,
  onConfirm,
  onDiscard,
  className,
}: ExtractionPreviewProps) {
  const [scan, setScan] = useState(payload.scan)
  const [submitting, setSubmitting] = useState(false)

  const updateMeasurement = (index: number, patch: Partial<NormalizedMeasurement>) => {
    setScan((current) => ({
      ...current,
      measurements: current.measurements.map((measurement, position) =>
        position === index ? { ...measurement, ...patch } : measurement,
      ),
    }))
  }

  const handleConfirm = async () => {
    if (!onConfirm) return
    setSubmitting(true)
    try {
      await onConfirm(scan)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section className={cn('panel', className)}>
      <header
        className="flex items-baseline justify-between border-b px-4 py-3"
        style={{
          borderColor: scan.requires_review ? '#D29922' : '#2A313C',
          backgroundColor: scan.requires_review ? 'rgba(210, 153, 34, 0.06)' : 'transparent',
        }}
      >
        <div className="flex items-baseline gap-3">
          <h2 className="label" style={{ color: scan.requires_review ? '#D29922' : undefined }}>
            {scan.requires_review ? 'Needs review' : 'Extraction complete'}
          </h2>
          <ManufacturerBadge
            manufacturer={scan.manufacturer}
            scannerModel={scan.scanner_model}
          />
        </div>
        <ConfidenceBar value={scan.overall_confidence} />
      </header>

      {/* ---- document level fields ---- */}
      <div className="grid grid-cols-2 gap-x-6 gap-y-4 border-b border-line px-4 py-4 sm:grid-cols-4">
        <Field label="Scan date">
          <input
            type="date"
            value={scan.scan_date}
            onChange={(event) => setScan({ ...scan, scan_date: event.target.value })}
            className="mono w-full border border-line bg-base px-2 py-1 text-sm text-ink"
          />
        </Field>

        <Field label="Patient reference">
          <span className="mono text-sm text-ink">{scan.patient_reference ?? 'Not found'}</span>
        </Field>

        <Field label="Scanner">
          <span className="text-sm text-ink">{scan.scanner_model ?? 'Not identified'}</span>
        </Field>

        <Field label="Read by">
          <span className="text-sm text-ink">
            {scan.extraction_source === 'pdf_text_layer' ? 'PDF text layer' : 'Cloud Vision OCR'}
          </span>
        </Field>
      </div>

      {/* ---- review reasons ---- */}
      {scan.review_reasons.length > 0 && (
        <ul className="border-b border-line bg-[rgba(210,153,34,0.04)] px-4 py-3">
          {scan.review_reasons.map((reason) => (
            <li key={reason} className="flex gap-2 py-[2px] text-xs text-ink-muted">
              <span className="mt-[6px] inline-block h-1 w-1 shrink-0" style={{ backgroundColor: '#D29922' }} />
              {reason}
            </li>
          ))}
        </ul>
      )}

      {/* ---- measurements ---- */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left">
              <Th>Site</Th>
              <Th align="right">BMD</Th>
              <Th align="right">sBMD</Th>
              <Th align="right">Printed T</Th>
              <Th align="right">Standardised T</Th>
              <Th align="right">Confidence</Th>
            </tr>
          </thead>
          <tbody>
            {scan.measurements.map((measurement, index) => (
              <tr key={measurement.site} className="border-b border-line last:border-b-0">
                <td className="px-4 py-3 text-ink">{SITE_LABEL[measurement.site]}</td>

                <td className="mono px-4 py-3 text-right text-ink-muted">
                  {measurement.bmd_g_cm2?.toFixed(3) ?? 'n/a'}
                </td>

                <td className="mono px-4 py-3 text-right text-ink-faint">
                  {measurement.standardized_bmd_mg_cm2?.toFixed(0) ?? 'n/a'}
                </td>

                <td className="mono px-4 py-3 text-right text-ink-muted">
                  {measurement.vendor_t_score?.toFixed(1) ?? 'n/a'}
                </td>

                <td className="px-4 py-3 text-right">
                  <input
                    type="number"
                    step="0.1"
                    value={measurement.t_score}
                    onChange={(event) =>
                      updateMeasurement(index, { t_score: Number(event.target.value) })
                    }
                    className="mono w-20 border border-line bg-base px-2 py-1 text-right text-sm text-ink"
                    aria-label={`Standardised T-score for ${SITE_LABEL[measurement.site]}`}
                  />
                  {!measurement.t_score_is_recomputed && (
                    <p className="mt-1 text-[10px] text-ink-faint">vendor value</p>
                  )}
                </td>

                <td className="px-4 py-3 text-right">
                  <ConfidenceBar value={measurement.confidence} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ---- per measurement notes ---- */}
      {scan.measurements.some((measurement) => measurement.notes.length > 0) && (
        <div className="border-t border-line px-4 py-3">
          {scan.measurements.flatMap((measurement) =>
            measurement.notes.map((note) => (
              <p key={`${measurement.site}-${note}`} className="py-[2px] text-[11px] text-ink-faint">
                <span className="text-ink-muted">{SITE_LABEL[measurement.site]}:</span> {note}
              </p>
            )),
          )}
        </div>
      )}

      <footer className="flex items-center justify-between gap-4 border-t border-line px-4 py-3">
        <p className="text-[11px] text-ink-faint">
          Extracted {formatScanDate(scan.scan_date)} &middot; document{' '}
          <span className="mono">{scan.document_id.slice(0, 8)}</span>
        </p>
        <div className="flex gap-2">
          {onDiscard && (
            <Button variant="ghost" onClick={onDiscard} type="button">
              Discard
            </Button>
          )}
          <Button variant="primary" onClick={handleConfirm} disabled={submitting} type="button">
            {submitting ? 'Saving' : 'Confirm and add to timeline'}
          </Button>
        </div>
      </footer>
    </section>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <Label>{label}</Label>
      <div className="mt-1">{children}</div>
    </div>
  )
}

function Th({ children, align = 'left' }: { children: React.ReactNode; align?: 'left' | 'right' }) {
  return (
    <th
      className={cn(
        'px-4 py-2 text-[11px] font-normal uppercase tracking-[0.14em] text-ink-faint',
        align === 'right' && 'text-right',
      )}
    >
      {children}
    </th>
  )
}

export default ExtractionPreview
