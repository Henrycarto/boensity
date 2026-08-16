/**
 * Types shared between the frontend and the three services.
 *
 * These mirror the pydantic models in each service's app/schemas.py. They are
 * hand-written rather than generated because the generated output for the
 * Envelope generic is unreadable, and this file is the contract a frontend
 * engineer actually reads.
 */

// ---------------------------------------------------------------------------
// Envelope
// ---------------------------------------------------------------------------

export interface ApiError {
  code: string
  message: string
  field?: string | null
}

export interface ApiMeta {
  service: string
  version: string
  generated_at?: string | null
  [key: string]: unknown
}

/** Every response from every service has this shape, success or failure. */
export interface Envelope<T> {
  data: T | null
  error: ApiError | null
  meta: ApiMeta
}

// ---------------------------------------------------------------------------
// Clinical enums
// ---------------------------------------------------------------------------

export type Sex = 'female' | 'male'

export type SkeletalSite = 'femoral_neck' | 'total_hip' | 'lumbar_spine' | 'forearm'

export type Manufacturer = 'hologic' | 'ge_lunar' | 'unknown'

export type DiagnosticBand = 'normal' | 'osteopenia' | 'osteoporosis'

export type TrendDirection = 'declining' | 'stable' | 'improving'

export type AlertSeverity = 'info' | 'warning' | 'critical'

/** WHO 1994 densitometric cutoffs. Referenced by the chart and the badges. */
export const OSTEOPENIA_THRESHOLD = -1.0
export const OSTEOPOROSIS_THRESHOLD = -2.5

/** BHOF intervention thresholds on the FRAX numbers. */
export const FRAX_MAJOR_THRESHOLD = 20.0
export const FRAX_HIP_THRESHOLD = 3.0

// ---------------------------------------------------------------------------
// Scans
// ---------------------------------------------------------------------------

export interface ScanObservation {
  scan_date: string
  t_score: number
  bmd_g_cm2?: number | null
  z_score?: number | null
  site: SkeletalSite
  manufacturer: Manufacturer
  scanner_model?: string | null
  source_document_id?: string | null
}

export interface PatientContext {
  patient_id: string
  age: number
  sex: Sex
  weight_kg: number
  height_cm: number
  previous_fracture: boolean
  parent_hip_fracture: boolean
  current_smoking: boolean
  glucocorticoids: boolean
  rheumatoid_arthritis: boolean
  secondary_osteoporosis: boolean
  alcohol_three_or_more_units: boolean
}

// ---------------------------------------------------------------------------
// Trajectory
// ---------------------------------------------------------------------------

export interface TrajectoryPoint {
  date: string
  years_from_baseline: number
  t_score: number
  is_projected: boolean
  ci_lower?: number | null
  ci_upper?: number | null
  band: DiagnosticBand
}

export interface VelocityReport {
  t_score_per_year: number
  bmd_percent_per_year?: number | null
  standard_error?: number | null
  ci_lower?: number | null
  ci_upper?: number | null
  r_squared?: number | null
  direction: TrendDirection
  exceeds_least_significant_change: boolean
  interpretation: string
}

export interface ThresholdCrossing {
  threshold_t_score: number
  band_entered: DiagnosticBand
  crossing_date: string | null
  years_until_crossing: number | null
  already_crossed: boolean
  confidence: 'high' | 'moderate' | 'low'
}

export interface Alert {
  code: string
  severity: AlertSeverity
  title: string
  detail: string
}

export interface TrajectoryResponse {
  patient_id: string
  site: SkeletalSite
  scan_count: number
  observation_span_years: number
  current_t_score: number
  current_band: DiagnosticBand
  points: TrajectoryPoint[]
  velocity: VelocityReport
  crossings: ThresholdCrossing[]
  alerts: Alert[]
  model_quality: string
}

// ---------------------------------------------------------------------------
// FRAX
// ---------------------------------------------------------------------------

export interface TreatmentDelta {
  agent: string
  major_osteoporotic_percent: number
  hip_percent: number
  absolute_risk_reduction_major: number
  absolute_risk_reduction_hip: number
  number_needed_to_treat_major: number | null
  evidence_note: string
}

export interface FraxResponse {
  patient_id: string
  major_osteoporotic_percent: number
  hip_percent: number
  bmd_included: boolean
  intervention_threshold_met: boolean
  threshold_basis: string
  contributing_factors: string[]
  treatment: TreatmentDelta | null
  calibration_note: string
}

export interface ProjectedFraxPoint {
  year_offset: number
  age: number
  projected_t_score: number
  major_osteoporotic_percent: number
  hip_percent: number
}

// ---------------------------------------------------------------------------
// Ingest
// ---------------------------------------------------------------------------

export type ExtractionSource = 'pdf_text_layer' | 'cloud_vision_ocr' | 'derived'

export type JobState =
  | 'queued'
  | 'extracting'
  | 'normalizing'
  | 'needs_review'
  | 'complete'
  | 'failed'

export interface NormalizedMeasurement {
  site: SkeletalSite
  bmd_g_cm2?: number | null
  standardized_bmd_mg_cm2?: number | null
  vendor_t_score?: number | null
  t_score: number
  z_score?: number | null
  t_score_is_recomputed: boolean
  confidence: number
  notes: string[]
}

export interface NormalizedScan {
  document_id: string
  patient_id?: string | null
  patient_reference?: string | null
  scan_date: string
  manufacturer: Manufacturer
  scanner_model?: string | null
  measurements: NormalizedMeasurement[]
  extraction_source: ExtractionSource
  overall_confidence: number
  requires_review: boolean
  review_reasons: string[]
}

export interface UploadAccepted {
  document_id: string
  job_id: string
  state: JobState
  filename: string
  size_bytes: number
  storage_key?: string | null
}

export interface JobStatus {
  job_id: string
  document_id: string
  state: JobState
  progress: number
  result?: NormalizedScan | null
  error?: string | null
}

export interface ExtractionPreviewPayload {
  document_id: string
  scan: NormalizedScan
  raw: unknown
  confirmable: boolean
}

// ---------------------------------------------------------------------------
// Sync
// ---------------------------------------------------------------------------

export type DrugClass =
  | 'oral_bisphosphonate'
  | 'iv_bisphosphonate'
  | 'denosumab'
  | 'serm'
  | 'anabolic'
  | 'supplement'

export type AdherenceBand = 'adherent' | 'partial' | 'non_adherent' | 'lapsed'

export type ReferralStatus = 'not_required' | 'suggested' | 'sent' | 'accepted' | 'declined'

export type InterventionChannel = 'sms' | 'email' | 'pharmacist_call' | 'physician_alert'

export interface Gap {
  start: string
  end: string
  days: number
  is_material: boolean
}

export interface AdherenceScore {
  patient_id: string
  drug_class: DrugClass
  proportion_days_covered: number
  medication_possession_ratio: number
  band: AdherenceBand
  days_covered: number
  days_in_period: number
  period_start: string
  period_end: string
  fill_count: number
  gaps: Gap[]
  longest_gap_days: number
  days_since_last_fill: number
  persistence_days: number
  risk_score: number | null
  risk_score_available: boolean
  interpretation: string
}

export interface InterventionSuggestion {
  channel: InterventionChannel
  urgency: string
  message: string
  rationale: string
}

export interface ReferralCandidate {
  pharmacy_id: string
  pharmacy_name: string
  distance_km?: number | null
  supports_drug_class: boolean
  accepts_new_referrals: boolean
  match_score: number | null
  match_score_available: boolean
}

export interface ReferralDecision {
  patient_id: string
  status: ReferralStatus
  reason: string
  candidates: ReferralCandidate[]
  selected_pharmacy_id: string | null
  routing_available: boolean
}

export interface AdherenceResponse {
  score: AdherenceScore
  interventions: InterventionSuggestion[]
  referral: ReferralDecision | null
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

/**
 * Band for a T-score. Duplicated from the Python side deliberately: the chart
 * colours points client-side while dragging and cannot wait on a round trip.
 * The boundaries must stay identical to trajectory_model.classify_band.
 */
export function classifyBand(tScore: number): DiagnosticBand {
  if (tScore <= OSTEOPOROSIS_THRESHOLD) return 'osteoporosis'
  if (tScore <= OSTEOPENIA_THRESHOLD) return 'osteopenia'
  return 'normal'
}

export const BAND_COLOR: Record<DiagnosticBand, string> = {
  normal: '#238636',
  osteopenia: '#D29922',
  osteoporosis: '#E05C2A',
}

export const BAND_LABEL: Record<DiagnosticBand, string> = {
  normal: 'Normal',
  osteopenia: 'Osteopenia',
  osteoporosis: 'Osteoporosis',
}

export const SITE_LABEL: Record<SkeletalSite, string> = {
  femoral_neck: 'Femoral neck',
  total_hip: 'Total hip',
  lumbar_spine: 'Lumbar spine',
  forearm: 'Forearm',
}

export const MANUFACTURER_LABEL: Record<Manufacturer, string> = {
  hologic: 'Hologic',
  ge_lunar: 'GE Lunar',
  unknown: 'Unknown',
}
