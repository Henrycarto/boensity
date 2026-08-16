/**
 * Typed client for the three Boensity services.
 *
 * Every service answers with {data, error, meta}, so unwrapping happens in one
 * place. Callers get the payload or an exception and never branch on envelope
 * shape themselves.
 */

import type {
  AdherenceResponse,
  Envelope,
  ExtractionPreviewPayload,
  FraxResponse,
  JobStatus,
  NormalizedScan,
  PatientContext,
  ProjectedFraxPoint,
  ReferralDecision,
  ScanObservation,
  SkeletalSite,
  TrajectoryResponse,
  UploadAccepted,
} from '@boensity/shared-types'

const TIMELINE_URL = process.env.NEXT_PUBLIC_TIMELINE_API_URL ?? 'http://localhost:8002'
const INGEST_URL = process.env.NEXT_PUBLIC_INGEST_API_URL ?? 'http://localhost:8001'
const SYNC_URL = process.env.NEXT_PUBLIC_SYNC_API_URL ?? 'http://localhost:8003'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly status: number,
    readonly field?: string | null,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function unwrap<T>(response: Response): Promise<T> {
  let envelope: Envelope<T>

  try {
    envelope = (await response.json()) as Envelope<T>
  } catch {
    throw new ApiError(
      `Service returned a non-JSON response (${response.status})`,
      'invalid_response',
      response.status,
    )
  }

  if (envelope.error) {
    throw new ApiError(
      envelope.error.message,
      envelope.error.code,
      response.status,
      envelope.error.field,
    )
  }

  if (envelope.data === null || envelope.data === undefined) {
    throw new ApiError('Service returned no data', 'empty_response', response.status)
  }

  return envelope.data
}

async function postJson<T>(url: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
    // Patient data must never be served from a cache.
    cache: 'no-store',
  })
  return unwrap<T>(response)
}

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal, cache: 'no-store' })
  return unwrap<T>(response)
}

// ---------------------------------------------------------------------------
// Timeline
// ---------------------------------------------------------------------------

export interface TrajectoryRequest {
  patient: PatientContext
  scans: ScanObservation[]
  site?: SkeletalSite
  projection_years?: number
}

export const timeline = {
  trajectory(request: TrajectoryRequest, signal?: AbortSignal): Promise<TrajectoryResponse> {
    return postJson<TrajectoryResponse>(`${TIMELINE_URL}/trajectory`, request, signal)
  },

  frax(
    request: {
      patient: PatientContext
      femoral_neck_t_score?: number | null
      include_treatment_delta?: boolean
      treatment?: 'bisphosphonate' | 'denosumab' | 'none'
    },
    signal?: AbortSignal,
  ): Promise<FraxResponse> {
    return postJson<FraxResponse>(`${TIMELINE_URL}/frax`, request, signal)
  },

  projectedFrax(
    request: {
      patient: PatientContext
      scans: ScanObservation[]
      projection_years?: number
      step_years?: number
    },
    signal?: AbortSignal,
  ): Promise<ProjectedFraxPoint[]> {
    return postJson<ProjectedFraxPoint[]>(`${TIMELINE_URL}/frax/projected`, request, signal)
  },

  storeScans(
    patientId: string,
    scans: ScanObservation[],
    signal?: AbortSignal,
  ): Promise<{ patient_id: string; stored: number }> {
    return postJson(`${TIMELINE_URL}/scans`, { patient_id: patientId, scans }, signal)
  },

  listScans(
    patientId: string,
    site: SkeletalSite = 'femoral_neck',
    signal?: AbortSignal,
  ): Promise<Record<string, unknown>[]> {
    return getJson(`${TIMELINE_URL}/scans/${encodeURIComponent(patientId)}?site=${site}`, signal)
  },

  atRiskCohort(
    site: SkeletalSite = 'femoral_neck',
    maxVelocity = -0.05,
    signal?: AbortSignal,
  ): Promise<Record<string, unknown>[]> {
    return getJson(`${TIMELINE_URL}/scans?site=${site}&max_velocity=${maxVelocity}`, signal)
  },
}

// ---------------------------------------------------------------------------
// Ingest
// ---------------------------------------------------------------------------

export const ingest = {
  async upload(
    file: File,
    options: { patientId?: string; sex?: string } = {},
  ): Promise<UploadAccepted> {
    const form = new FormData()
    form.append('file', file)
    if (options.patientId) form.append('patient_id', options.patientId)
    if (options.sex) form.append('sex', options.sex)

    const response = await fetch(`${INGEST_URL}/upload`, {
      method: 'POST',
      body: form,
      cache: 'no-store',
    })
    return unwrap<UploadAccepted>(response)
  },

  /** Inline extraction, used when a clinician is waiting on the review screen. */
  async extract(
    file: File,
    options: { patientId?: string; sex?: string } = {},
  ): Promise<ExtractionPreviewPayload> {
    const form = new FormData()
    form.append('file', file)
    if (options.patientId) form.append('patient_id', options.patientId)
    if (options.sex) form.append('sex', options.sex)

    const response = await fetch(`${INGEST_URL}/extract`, {
      method: 'POST',
      body: form,
      cache: 'no-store',
    })
    return unwrap<ExtractionPreviewPayload>(response)
  },

  jobStatus(jobId: string, signal?: AbortSignal): Promise<JobStatus> {
    return getJson<JobStatus>(`${INGEST_URL}/jobs/${encodeURIComponent(jobId)}`, signal)
  },

  confirm(scan: NormalizedScan, signal?: AbortSignal): Promise<Record<string, unknown>> {
    return postJson(`${INGEST_URL}/confirm`, scan, signal)
  },
}

// ---------------------------------------------------------------------------
// Sync
// ---------------------------------------------------------------------------

export const sync = {
  adherenceForPatient(patientId: string, signal?: AbortSignal): Promise<AdherenceResponse> {
    return getJson<AdherenceResponse>(
      `${SYNC_URL}/adherence/${encodeURIComponent(patientId)}`,
      signal,
    )
  },

  scoreAdherence(
    request: { patient_id: string; fills: unknown[]; period_start?: string; period_end?: string },
    signal?: AbortSignal,
  ): Promise<AdherenceResponse> {
    return postJson<AdherenceResponse>(`${SYNC_URL}/adherence`, request, signal)
  },

  evaluateReferral(
    request: { patient_id: string; fills: unknown[] },
    signal?: AbortSignal,
  ): Promise<ReferralDecision> {
    return postJson<ReferralDecision>(`${SYNC_URL}/referrals/evaluate`, request, signal)
  },

  sendReferral(
    request: { patient_id: string; pharmacy_id: string; note?: string },
    signal?: AbortSignal,
  ): Promise<Record<string, unknown>> {
    return postJson(`${SYNC_URL}/referrals`, request, signal)
  },
}

export const SERVICE_URLS = {
  timeline: TIMELINE_URL,
  ingest: INGEST_URL,
  sync: SYNC_URL,
} as const
