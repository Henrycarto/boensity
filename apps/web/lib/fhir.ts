/**
 * SMART on FHIR launch wiring for the Next.js app.
 *
 * The client package holds the protocol. This file holds the parts that are
 * specific to running it inside Next: where the launch state lives between the
 * redirect out and the redirect back, and how FHIR Observations become the
 * scan series the Timeline service expects.
 *
 * Server-only. The code verifier and the access token never reach the browser,
 * which is what keeps this a real OAuth flow rather than one with the secret
 * sitting in localStorage.
 */

import 'server-only'
import { cookies } from 'next/headers'
import {
  FhirClient,
  ageFromBirthDate,
  buildAuthorizeUrl,
  deriveCodeChallenge,
  discoverSmartConfiguration,
  exchangeCodeForToken,
  generateCodeVerifier,
  generateState,
  refreshAccessToken,
  sessionFromToken,
  type FhirObservation,
  type SmartSession,
} from '@boensity/fhir-client'
import type { Manufacturer, ScanObservation, SkeletalSite } from '@boensity/shared-types'
import { LOINC } from '@boensity/fhir-client'

const LAUNCH_COOKIE = 'boensity_smart_launch'
const SESSION_COOKIE = 'boensity_smart_session'

// Ten minutes is generous for a redirect to an authorization server and back,
// and short enough that an abandoned launch does not leave a usable verifier.
const LAUNCH_TTL_SECONDS = 600

export function fhirConfig() {
  return {
    clientId: process.env.FHIR_CLIENT_ID ?? 'boensity-local',
    clientSecret: process.env.FHIR_CLIENT_SECRET || undefined,
    redirectUri: process.env.FHIR_REDIRECT_URI ?? 'http://localhost:3000/api/fhir/callback',
    scope:
      process.env.FHIR_SCOPES ??
      'launch openid fhirUser offline_access patient/Patient.read patient/Observation.read',
    defaultIss: process.env.FHIR_ISSUER ?? 'https://launch.smarthealthit.org/v/r4/fhir',
  }
}

interface PendingLaunch {
  iss: string
  state: string
  codeVerifier: string
}

const secureCookies = process.env.NODE_ENV === 'production'

/**
 * Begin a launch and return the URL to send the browser to.
 *
 * Handles both modes: pass the EHR's `launch` token for an EHR launch, omit it
 * for standalone. The verifier and state are stored in an httpOnly cookie, not
 * in a module-level map, so the flow survives a serverless cold start between
 * the two halves of the redirect.
 */
export async function beginSmartLaunch(options: {
  iss?: string
  launch?: string
}): Promise<string> {
  const config = fhirConfig()
  const iss = options.iss ?? config.defaultIss

  const smartConfig = await discoverSmartConfiguration(iss)
  const codeVerifier = generateCodeVerifier()
  const codeChallenge = await deriveCodeChallenge(codeVerifier)
  const state = generateState()

  const pending: PendingLaunch = { iss, state, codeVerifier }

  cookies().set(LAUNCH_COOKIE, JSON.stringify(pending), {
    httpOnly: true,
    secure: secureCookies,
    sameSite: 'lax',
    path: '/',
    maxAge: LAUNCH_TTL_SECONDS,
  })

  return buildAuthorizeUrl(smartConfig, {
    iss,
    clientId: config.clientId,
    redirectUri: config.redirectUri,
    scope: config.scope,
    launch: options.launch,
    state,
    codeChallenge,
  })
}

/**
 * Finish the launch: verify state, exchange the code, store the session.
 *
 * The state check is the CSRF defence on the redirect back, and a mismatch
 * means the callback did not originate from a launch this server started.
 */
export async function completeSmartLaunch(options: {
  code: string
  state: string
}): Promise<SmartSession> {
  const config = fhirConfig()
  const raw = cookies().get(LAUNCH_COOKIE)?.value

  if (!raw) {
    throw new Error('no pending SMART launch, the flow expired or never started here')
  }

  const pending = JSON.parse(raw) as PendingLaunch

  if (pending.state !== options.state) {
    throw new Error('SMART launch state mismatch, refusing the callback')
  }

  const smartConfig = await discoverSmartConfiguration(pending.iss)
  const token = await exchangeCodeForToken({
    config: smartConfig,
    code: options.code,
    redirectUri: config.redirectUri,
    clientId: config.clientId,
    clientSecret: config.clientSecret,
    codeVerifier: pending.codeVerifier,
  })

  const session = sessionFromToken(token, pending.iss)

  cookies().delete(LAUNCH_COOKIE)
  cookies().set(SESSION_COOKIE, JSON.stringify(session), {
    httpOnly: true,
    secure: secureCookies,
    sameSite: 'lax',
    path: '/',
    maxAge: token.expires_in,
  })

  return session
}

export function readSmartSession(): SmartSession | null {
  const raw = cookies().get(SESSION_COOKIE)?.value
  if (!raw) return null
  try {
    return JSON.parse(raw) as SmartSession
  } catch {
    return null
  }
}

/** Session with a valid access token, refreshing first if it has expired. */
export async function getAuthenticatedClient(): Promise<FhirClient | null> {
  let session = readSmartSession()
  if (!session) return null

  if (Date.now() >= session.expiresAt) {
    if (!session.refreshToken) return null

    const config = fhirConfig()
    const smartConfig = await discoverSmartConfiguration(session.iss)
    const token = await refreshAccessToken({
      config: smartConfig,
      refreshToken: session.refreshToken,
      clientId: config.clientId,
      clientSecret: config.clientSecret,
    })

    session = sessionFromToken(token, session.iss)
    cookies().set(SESSION_COOKIE, JSON.stringify(session), {
      httpOnly: true,
      secure: secureCookies,
      sameSite: 'lax',
      path: '/',
      maxAge: token.expires_in,
    })
  }

  return new FhirClient(session)
}

export function endSmartSession(): void {
  cookies().delete(SESSION_COOKIE)
  cookies().delete(LAUNCH_COOKIE)
}

// ---------------------------------------------------------------------------
// Mapping FHIR to the internal scan schema
// ---------------------------------------------------------------------------

const SITE_BY_LOINC: Record<string, SkeletalSite> = {
  [LOINC.FEMORAL_NECK_BMD]: 'femoral_neck',
  [LOINC.TOTAL_HIP_BMD]: 'total_hip',
  [LOINC.LUMBAR_SPINE_BMD]: 'lumbar_spine',
}

// SNOMED body site codes, used when an Observation carries a generic DXA code
// and puts the anatomy in bodySite instead. Both patterns occur in production.
const SITE_BY_SNOMED: Record<string, SkeletalSite> = {
  '29627003': 'femoral_neck',
  '24136001': 'total_hip',
  '122494005': 'lumbar_spine',
  '62413002': 'forearm',
}

function siteFor(observation: FhirObservation): SkeletalSite | null {
  for (const coding of observation.code.coding ?? []) {
    if (coding.code && SITE_BY_LOINC[coding.code]) return SITE_BY_LOINC[coding.code] as SkeletalSite
  }
  for (const coding of observation.bodySite?.coding ?? []) {
    if (coding.code && SITE_BY_SNOMED[coding.code]) return SITE_BY_SNOMED[coding.code] as SkeletalSite
  }
  return null
}

function isTScoreCode(observation: FhirObservation): boolean {
  return (observation.code.coding ?? []).some((coding) => coding.code === LOINC.DXA_T_SCORE)
}

/**
 * Turn FHIR Observations into scan observations the Timeline service accepts.
 *
 * EHRs model DXA results two ways: a BMD Observation with the T-score as a
 * component, or separate BMD and T-score Observations sharing a date. Both are
 * handled by keying on date plus site and merging whatever arrives.
 *
 * Manufacturer is left unknown because FHIR has no standard place for it. That
 * is honest rather than lossy: schema_mapper refuses to standardise an unknown
 * vendor, so an EHR-sourced series is flagged as unstandardised instead of
 * being silently converted with the wrong regression.
 */
export function observationsToScans(observations: FhirObservation[]): ScanObservation[] {
  const merged = new Map<string, ScanObservation>()

  for (const observation of observations) {
    const site = siteFor(observation)
    const effective = observation.effectiveDateTime
    if (!site || !effective) continue

    const scanDate = effective.slice(0, 10)
    const key = `${scanDate}|${site}`

    const existing: ScanObservation = merged.get(key) ?? {
      scan_date: scanDate,
      t_score: Number.NaN,
      bmd_g_cm2: null,
      site,
      manufacturer: 'unknown' as Manufacturer,
      source_document_id: observation.id,
    }

    const value = observation.valueQuantity?.value

    if (isTScoreCode(observation) && typeof value === 'number') {
      existing.t_score = value
    } else if (typeof value === 'number') {
      existing.bmd_g_cm2 = value
    }

    // The common shape: a BMD Observation carrying the T-score alongside.
    for (const component of observation.component ?? []) {
      const isTScore = (component.code.coding ?? []).some(
        (coding) => coding.code === LOINC.DXA_T_SCORE,
      )
      if (isTScore && typeof component.valueQuantity?.value === 'number') {
        existing.t_score = component.valueQuantity.value
      }
    }

    merged.set(key, existing)
  }

  // A record with no T-score cannot be plotted and cannot be derived without
  // knowing the reference database, so it is dropped rather than guessed at.
  return Array.from(merged.values())
    .filter((scan) => Number.isFinite(scan.t_score))
    .sort((a, b) => a.scan_date.localeCompare(b.scan_date))
}

export { ageFromBirthDate }
