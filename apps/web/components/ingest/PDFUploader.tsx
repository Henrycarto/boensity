'use client'

/**
 * DEXA report upload.
 *
 * Takes a stack of PDFs, because that is how these arrive: a clinician has
 * fifteen years of a patient's scans in a folder, not one file. Each is
 * extracted independently so one unreadable fax does not block the rest.
 */

import { useCallback, useRef, useState } from 'react'
import type { ExtractionPreviewPayload } from '@boensity/shared-types'
import { ApiError, ingest } from '@/lib/api'
import { Button, Label } from '@/components/ui'
import { cn } from '@/lib/utils'

type FileState = 'pending' | 'extracting' | 'done' | 'failed'

interface QueuedFile {
  id: string
  file: File
  state: FileState
  error?: string
  result?: ExtractionPreviewPayload
}

interface PDFUploaderProps {
  patientId?: string
  sex?: 'female' | 'male'
  onExtracted?: (payload: ExtractionPreviewPayload) => void
  className?: string
}

const MAX_BYTES = 25 * 1024 * 1024

export function PDFUploader({ patientId, sex, onExtracted, className }: PDFUploaderProps) {
  const [queue, setQueue] = useState<QueuedFile[]>([])
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const processFile = useCallback(
    async (entry: QueuedFile) => {
      setQueue((current) =>
        current.map((item) => (item.id === entry.id ? { ...item, state: 'extracting' } : item)),
      )

      try {
        const result = await ingest.extract(entry.file, { patientId, sex })
        setQueue((current) =>
          current.map((item) =>
            item.id === entry.id ? { ...item, state: 'done', result } : item,
          ),
        )
        onExtracted?.(result)
      } catch (error) {
        const message =
          error instanceof ApiError
            ? error.message
            : error instanceof Error
              ? error.message
              : 'Extraction failed'
        setQueue((current) =>
          current.map((item) =>
            item.id === entry.id ? { ...item, state: 'failed', error: message } : item,
          ),
        )
      }
    },
    [patientId, sex, onExtracted],
  )

  const acceptFiles = useCallback(
    (files: FileList | null) => {
      if (!files) return

      const accepted: QueuedFile[] = []
      for (const file of Array.from(files)) {
        // Checked here as well as server-side so the clinician gets the reason
        // immediately rather than after an upload round trip.
        if (!file.name.toLowerCase().endsWith('.pdf') && file.type !== 'application/pdf') {
          accepted.push({
            id: `${file.name}-${file.lastModified}`,
            file,
            state: 'failed',
            error: 'Not a PDF',
          })
          continue
        }
        if (file.size > MAX_BYTES) {
          accepted.push({
            id: `${file.name}-${file.lastModified}`,
            file,
            state: 'failed',
            error: `Too large (${Math.round(file.size / 1024 / 1024)} MB, limit 25 MB)`,
          })
          continue
        }
        accepted.push({ id: `${file.name}-${file.lastModified}`, file, state: 'pending' })
      }

      setQueue((current) => [...current, ...accepted])
      accepted.filter((entry) => entry.state === 'pending').forEach(processFile)
    },
    [processFile],
  )

  return (
    <section className={cn('panel', className)}>
      <div className="panel-header">
        <h2 className="label">Upload DEXA reports</h2>
        <Label>Hologic and GE Lunar</Label>
      </div>

      <div
        onDragOver={(event) => {
          event.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault()
          setDragging(false)
          acceptFiles(event.dataTransfer.files)
        }}
        className={cn(
          'm-4 border border-dashed px-6 py-10 text-center transition-colors',
          dragging ? 'border-danger bg-[rgba(224,92,42,0.06)]' : 'border-line',
        )}
      >
        <p className="text-sm text-ink">Drop scan PDFs here</p>
        <p className="mx-auto mt-2 max-w-sm text-xs leading-relaxed text-ink-faint">
          Text-native exports are read directly. Scanned or faxed reports go through OCR, which
          takes a few seconds longer.
        </p>

        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          multiple
          className="sr-only"
          onChange={(event) => {
            acceptFiles(event.target.files)
            // Reset so re-selecting the same file fires change again.
            event.target.value = ''
          }}
        />

        <Button
          variant="primary"
          className="mt-4"
          onClick={() => inputRef.current?.click()}
          type="button"
        >
          Select files
        </Button>
      </div>

      {queue.length > 0 && (
        <ul className="border-t border-line">
          {queue.map((entry) => (
            <li
              key={entry.id}
              className="flex items-center justify-between gap-4 border-b border-line px-4 py-3 last:border-b-0"
            >
              <div className="min-w-0">
                <p className="truncate text-sm text-ink">{entry.file.name}</p>
                <p className="mono mt-1 text-[11px] text-ink-faint">
                  {(entry.file.size / 1024).toFixed(0)} KB
                  {entry.result && (
                    <>
                      {' '}
                      &middot; {entry.result.scan.manufacturer.replace('_', ' ')} &middot;{' '}
                      {entry.result.scan.measurements.length} site
                      {entry.result.scan.measurements.length === 1 ? '' : 's'}
                    </>
                  )}
                </p>
              </div>

              <StateTag state={entry.state} error={entry.error} needsReview={entry.result?.scan.requires_review} />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function StateTag({
  state,
  error,
  needsReview,
}: {
  state: FileState
  error?: string
  needsReview?: boolean
}) {
  if (state === 'extracting') {
    return <span className="mono shrink-0 text-[11px] text-ink-muted">Extracting</span>
  }

  if (state === 'failed') {
    return (
      <span className="mono shrink-0 text-[11px]" style={{ color: '#E05C2A' }} title={error}>
        {error ?? 'Failed'}
      </span>
    )
  }

  if (state === 'done') {
    return needsReview ? (
      <span className="mono shrink-0 text-[11px]" style={{ color: '#D29922' }}>
        Needs review
      </span>
    ) : (
      <span className="mono shrink-0 text-[11px]" style={{ color: '#238636' }}>
        Extracted
      </span>
    )
  }

  return <span className="mono shrink-0 text-[11px] text-ink-faint">Queued</span>
}

export default PDFUploader
