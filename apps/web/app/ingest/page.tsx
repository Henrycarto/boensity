'use client'

import { useState } from 'react'
import type { ExtractionPreviewPayload, NormalizedScan } from '@boensity/shared-types'
import { PDFUploader } from '@/components/ingest/PDFUploader'
import { ExtractionPreview } from '@/components/ingest/ExtractionPreview'
import { EmptyState, Label } from '@/components/ui'
import { ApiError, ingest } from '@/lib/api'

/**
 * Ingest screen.
 *
 * Upload on the left, review on the right. The review pane is the point: a
 * physician sees what was read, corrects it if the extractor got it wrong, and
 * only then does anything reach the patient timeline.
 */
export default function IngestPage() {
  const [extractions, setExtractions] = useState<ExtractionPreviewPayload[]>([])
  const [activeIndex, setActiveIndex] = useState(0)
  const [message, setMessage] = useState<{ text: string; ok: boolean } | null>(null)

  const active = extractions[activeIndex]

  const handleExtracted = (payload: ExtractionPreviewPayload) => {
    setExtractions((current) => {
      const next = [...current, payload]
      setActiveIndex(next.length - 1)
      return next
    })
  }

  const handleConfirm = async (scan: NormalizedScan) => {
    if (!scan.patient_id) {
      setMessage({
        text: 'Assign this scan to a patient before saving it to a timeline.',
        ok: false,
      })
      return
    }

    try {
      await ingest.confirm(scan)
      setMessage({ text: `Saved ${scan.measurements.length} measurements.`, ok: true })
      setExtractions((current) => current.filter((_, index) => index !== activeIndex))
      setActiveIndex(0)
    } catch (error) {
      setMessage({
        text: error instanceof ApiError ? error.message : 'Could not save to the timeline service.',
        ok: false,
      })
    }
  }

  return (
    <div className="mx-auto max-w-[1600px] px-6 py-6">
      <header className="mb-6 border-b border-line pb-4">
        <h1 className="text-lg tracking-tight text-ink">Ingest</h1>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-ink-muted">
          Drop in a patient&apos;s DEXA reports from any year and any manufacturer. Values are read
          off the report, converted to standardised BMD, and rescored against one reference so the
          whole series sits on a single axis. Nothing is written to a timeline until it is
          confirmed here.
        </p>
      </header>

      {message && (
        <div
          className="mb-px border px-4 py-3 text-xs"
          style={{
            borderColor: message.ok ? '#238636' : '#E05C2A',
            color: message.ok ? '#238636' : '#E05C2A',
            backgroundColor: message.ok ? 'rgba(35,134,54,0.06)' : 'rgba(224,92,42,0.06)',
          }}
        >
          {message.text}
        </div>
      )}

      <div className="grid gap-px lg:grid-cols-[minmax(340px,1fr)_2fr]">
        <div className="space-y-px">
          <PDFUploader onExtracted={handleExtracted} />

          {extractions.length > 1 && (
            <section className="panel">
              <div className="panel-header">
                <h2 className="label">Extracted documents</h2>
                <Label>{extractions.length}</Label>
              </div>
              <ul>
                {extractions.map((extraction, index) => (
                  <li key={extraction.document_id} className="border-b border-line last:border-b-0">
                    <button
                      type="button"
                      onClick={() => setActiveIndex(index)}
                      className={`flex w-full items-center justify-between px-4 py-2 text-left text-xs ${
                        index === activeIndex ? 'bg-surface-raised text-ink' : 'text-ink-muted'
                      }`}
                    >
                      <span className="mono">{extraction.scan.scan_date}</span>
                      <span className="capitalize">
                        {extraction.scan.manufacturer.replace('_', ' ')}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>

        {active ? (
          <ExtractionPreview
            key={active.document_id}
            payload={active}
            onConfirm={handleConfirm}
            onDiscard={() => {
              setExtractions((current) => current.filter((_, index) => index !== activeIndex))
              setActiveIndex(0)
            }}
          />
        ) : (
          <div className="panel flex items-center justify-center p-8">
            <EmptyState
              title="No document loaded"
              detail="Upload a DEXA report to see what was read off it. Text-native exports return in under a second. Scanned reports go through OCR and take a few seconds longer."
            />
          </div>
        )}
      </div>
    </div>
  )
}
