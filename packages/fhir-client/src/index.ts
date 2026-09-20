/**
 * SMART on FHIR OAuth 2.0 client.
 *
 * Implements the SMART App Launch 2.0 flow properly: authorization server
 * discovery, PKCE, the authorization code exchange, refresh, and the launch
 * context that comes back with the token.
 *
 * Both launch modes are supported because hospitals use both.
 *
 *   EHR launch        the clinician is already in Epic or Cerner and opens
 *                     Boensity from inside a patient chart. The EHR hands us
 *                     an `iss` and a `launch` token, and the patient comes
 *                     back in the token response. No patient picker.
 *
 *   Standalone launch the clinician opens Boensity directly and authenticates
 *                     against the EHR. Scopes include launch/patient so the
 *                     EHR shows its own patient picker.
 *
 * PKCE is mandatory, not optional. SMART App Launch 2.0 requires it for public
 * clients, and Boensity's browser-facing launch is a public client. The code
 * verifier never leaves the server session.
 */

export * from './issuers'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SmartConfiguration {
  authorization_endpoint: string
  token_endpoint: string
  introspection_endpoint?: string
  revocation_endpoint?: string
  capabilities?: string[]
  code_challenge_methods_supported?: string[]
  scopes_supported?: string[]
}

export interface LaunchParams {
  /** FHIR server base URL, supplied by the EHR as `iss`. */
  iss: string
  clientId: string
  redirectUri: string
  scope: string
  /** Opaque launch token from an EHR launch. Absent on standalone launch. */
  launch?: string
  state: string
  codeChallenge: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
  scope: string
  refresh_token?: string
  id_token?: string
  /** Launch context. Present when the token was issued with patient scope. */
  patient?: string
  encounter?: string
  fhirUser?: string
  need_patient_banner?: boolean
}

export interface SmartSession {
  accessToken: string
  refreshToken?: string
  /** Absolute expiry as epoch milliseconds, so no clock arithmetic at use. */
  expiresAt: number
  scope: string
  iss: string
  patientId?: string
  encounterId?: string
  fhirUser?: string
}

export class FhirError extends Error {
  constructor(
    message: string,
    readonly status?: number,
    readonly operationOutcome?: unknown,
  ) {
    super(message)
    this.name = 'FhirError'
  }
}

// ---------------------------------------------------------------------------
// PKCE
// ---------------------------------------------------------------------------

const PKCE_VERIFIER_BYTES = 32

function base64UrlEncode(bytes: Uint8Array): string {
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/** Cryptographically random verifier. Store server-side against the state. */
export function generateCodeVerifier(): string {
  const bytes = new Uint8Array(PKCE_VERIFIER_BYTES)
  crypto.getRandomValues(bytes)
  return base64UrlEncode(bytes)
}

export async function deriveCodeChallenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))
  return base64UrlEncode(new Uint8Array(digest))
}

/** Random state, which is the CSRF defence on the redirect back. */
export function generateState(): string {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return base64UrlEncode(bytes)
}

// ---------------------------------------------------------------------------
// Discovery
// ---------------------------------------------------------------------------

/**
 * Discovered documents are cached per issuer for an hour.
 *
 * A launch touches discovery three times (authorize, token exchange, refresh)
 * and the endpoints do not change between them. The cache is per process, so
 * a serverless cold start simply refetches, and `force` skips it for tests
 * and diagnostics.
 */
const DISCOVERY_TTL_MS = 60 * 60 * 1000
const discoveryCache = new Map<string, { config: SmartConfiguration; fetchedAt: number }>()

export function clearSmartConfigurationCache(): void {
  discoveryCache.clear()
}

function isAbsoluteHttpUrl(value: unknown): value is string {
  if (typeof value !== 'string') return false
  try {
    const url = new URL(value)
    return url.protocol === 'https:' || url.protocol === 'http:'
  } catch {
    return false
  }
}

/**
 * Refuse a discovery document this client cannot use safely.
 *
 * PKCE with S256 is always sent. A server that advertises its supported
 * challenge methods and leaves S256 out would silently fail the exchange, or
 * worse accept `plain`, so it is rejected here rather than downgraded.
 */
export function assertSmartConfigurationUsable(
  config: SmartConfiguration,
  iss: string,
): SmartConfiguration {
  if (!isAbsoluteHttpUrl(config.authorization_endpoint)) {
    throw new FhirError(`smart-configuration for ${iss} has no usable authorization_endpoint`)
  }
  if (!isAbsoluteHttpUrl(config.token_endpoint)) {
    throw new FhirError(`smart-configuration for ${iss} has no usable token_endpoint`)
  }
  const methods = config.code_challenge_methods_supported
  if (Array.isArray(methods) && !methods.includes('S256')) {
    throw new FhirError(
      `server at ${iss} does not support PKCE S256 (advertises ${methods.join(', ') || 'none'})`,
    )
  }
  return config
}

/**
 * Find the authorization and token endpoints for a FHIR server.
 *
 * The well-known document is the modern route. The CapabilityStatement
 * extension is the older one, and enough production servers still only expose
 * that to make the fallback necessary rather than defensive.
 */
export async function discoverSmartConfiguration(
  iss: string,
  options: { force?: boolean } = {},
): Promise<SmartConfiguration> {
  const base = iss.replace(/\/+$/, '')

  const cached = discoveryCache.get(base)
  if (!options.force && cached && Date.now() - cached.fetchedAt < DISCOVERY_TTL_MS) {
    return cached.config
  }

  const config = assertSmartConfigurationUsable(await fetchSmartConfiguration(base), base)
  discoveryCache.set(base, { config, fetchedAt: Date.now() })
  return config
}

async function fetchSmartConfiguration(base: string): Promise<SmartConfiguration> {
  const wellKnown = await fetch(`${base}/.well-known/smart-configuration`, {
    headers: { Accept: 'application/json' },
    cache: 'no-store',
  }).catch(() => null)

  if (wellKnown?.ok) {
    const config = (await wellKnown.json()) as SmartConfiguration
    if (config.authorization_endpoint && config.token_endpoint) return config
  }

  const capability = await fetch(`${base}/metadata`, {
    headers: { Accept: 'application/fhir+json' },
    cache: 'no-store',
  })

  if (!capability.ok) {
    throw new FhirError(
      `could not discover SMART endpoints for ${base}`,
      capability.status,
    )
  }

  const statement = (await capability.json()) as CapabilityStatement
  const oauthUris = statement.rest
    ?.flatMap((rest) => rest.security?.extension ?? [])
    .find(
      (extension) =>
        extension.url === 'http://fhir-registry.smarthealthit.org/StructureDefinition/oauth-uris',
    )

  const authorize = oauthUris?.extension?.find((entry) => entry.url === 'authorize')?.valueUri
  const token = oauthUris?.extension?.find((entry) => entry.url === 'token')?.valueUri

  if (!authorize || !token) {
    throw new FhirError(`server at ${base} does not advertise SMART OAuth endpoints`)
  }

  return { authorization_endpoint: authorize, token_endpoint: token }
}

interface CapabilityStatement {
  rest?: Array<{
    security?: {
      extension?: Array<{
        url: string
        extension?: Array<{ url: string; valueUri?: string }>
      }>
    }
  }>
}

// ---------------------------------------------------------------------------
// Authorization
// ---------------------------------------------------------------------------

export function buildAuthorizeUrl(
  config: SmartConfiguration,
  params: LaunchParams,
): string {
  const url = new URL(config.authorization_endpoint)

  url.searchParams.set('response_type', 'code')
  url.searchParams.set('client_id', params.clientId)
  url.searchParams.set('redirect_uri', params.redirectUri)
  url.searchParams.set('scope', params.scope)
  url.searchParams.set('state', params.state)
  // aud must equal the FHIR base URL. Servers reject the request without it,
  // and it is what stops a token minted for one server being replayed at another.
  url.searchParams.set('aud', params.iss)
  url.searchParams.set('code_challenge', params.codeChallenge)
  url.searchParams.set('code_challenge_method', 'S256')

  if (params.launch) url.searchParams.set('launch', params.launch)

  return url.toString()
}

export async function exchangeCodeForToken(options: {
  config: SmartConfiguration
  code: string
  redirectUri: string
  clientId: string
  codeVerifier: string
  /** Confidential clients only. A browser-launched app must not send this. */
  clientSecret?: string
}): Promise<TokenResponse> {
  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    code: options.code,
    redirect_uri: options.redirectUri,
    client_id: options.clientId,
    code_verifier: options.codeVerifier,
  })

  const headers: Record<string, string> = {
    'Content-Type': 'application/x-www-form-urlencoded',
    Accept: 'application/json',
  }

  if (options.clientSecret) {
    headers.Authorization = `Basic ${btoa(`${options.clientId}:${options.clientSecret}`)}`
  }

  const response = await fetch(options.config.token_endpoint, {
    method: 'POST',
    headers,
    body,
    cache: 'no-store',
  })

  if (!response.ok) {
    const detail = await response.text()
    throw new FhirError(`token exchange failed: ${detail}`, response.status)
  }

  return (await response.json()) as TokenResponse
}

export async function refreshAccessToken(options: {
  config: SmartConfiguration
  refreshToken: string
  clientId: string
  clientSecret?: string
  scope?: string
}): Promise<TokenResponse> {
  const body = new URLSearchParams({
    grant_type: 'refresh_token',
    refresh_token: options.refreshToken,
    client_id: options.clientId,
  })

  if (options.scope) body.set('scope', options.scope)

  const headers: Record<string, string> = {
    'Content-Type': 'application/x-www-form-urlencoded',
    Accept: 'application/json',
  }

  if (options.clientSecret) {
    headers.Authorization = `Basic ${btoa(`${options.clientId}:${options.clientSecret}`)}`
  }

  const response = await fetch(options.config.token_endpoint, {
    method: 'POST',
    headers,
    body,
    cache: 'no-store',
  })

  if (!response.ok) {
    throw new FhirError(`refresh failed: ${await response.text()}`, response.status)
  }

  return (await response.json()) as TokenResponse
}

export function sessionFromToken(token: TokenResponse, iss: string): SmartSession {
  return {
    accessToken: token.access_token,
    refreshToken: token.refresh_token,
    // Sixty second safety margin so a request started just before expiry does
    // not arrive just after it.
    expiresAt: Date.now() + (token.expires_in - 60) * 1000,
    scope: token.scope,
    iss,
    patientId: token.patient,
    encounterId: token.encounter,
    fhirUser: token.fhirUser,
  }
}

// ---------------------------------------------------------------------------
// FHIR resource access
// ---------------------------------------------------------------------------

export interface FhirBundle<T> {
  resourceType: 'Bundle'
  type: string
  total?: number
  entry?: Array<{ fullUrl?: string; resource: T }>
  link?: Array<{ relation: string; url: string }>
}

export interface FhirPatient {
  resourceType: 'Patient'
  id: string
  name?: Array<{ family?: string; given?: string[]; text?: string }>
  gender?: 'male' | 'female' | 'other' | 'unknown'
  birthDate?: string
  identifier?: Array<{ system?: string; value?: string }>
}

export interface FhirObservation {
  resourceType: 'Observation'
  id: string
  status: string
  effectiveDateTime?: string
  code: { coding?: Array<{ system?: string; code?: string; display?: string }>; text?: string }
  valueQuantity?: { value?: number; unit?: string; system?: string; code?: string }
  component?: Array<{
    code: { coding?: Array<{ system?: string; code?: string; display?: string }> }
    valueQuantity?: { value?: number; unit?: string }
  }>
  bodySite?: { coding?: Array<{ system?: string; code?: string; display?: string }> }
}

/** LOINC codes for the values Boensity reads out of an EHR. */
export const LOINC = {
  FEMORAL_NECK_BMD: '24701-5',
  LUMBAR_SPINE_BMD: '24700-7',
  TOTAL_HIP_BMD: '38265-5',
  DXA_T_SCORE: '38261-4',
  BODY_WEIGHT: '29463-7',
  BODY_HEIGHT: '8302-2',
} as const

export class FhirClient {
  constructor(private readonly session: SmartSession) {}

  get patientId(): string | undefined {
    return this.session.patientId
  }

  get expired(): boolean {
    return Date.now() >= this.session.expiresAt
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    if (this.expired) {
      throw new FhirError('SMART session has expired, refresh before making requests', 401)
    }

    const base = this.session.iss.replace(/\/+$/, '')
    const url = path.startsWith('http') ? path : `${base}/${path.replace(/^\/+/, '')}`

    const response = await fetch(url, {
      ...init,
      headers: {
        Authorization: `Bearer ${this.session.accessToken}`,
        Accept: 'application/fhir+json',
        ...(init?.headers ?? {}),
      },
      cache: 'no-store',
    })

    if (!response.ok) {
      // FHIR servers return the reason in an OperationOutcome. Passing it
      // through turns "403 Forbidden" into a scope name someone can act on.
      let outcome: unknown
      try {
        outcome = await response.json()
      } catch {
        outcome = undefined
      }
      throw new FhirError(
        `FHIR request failed: ${response.status} ${response.statusText}`,
        response.status,
        outcome,
      )
    }

    return (await response.json()) as T
  }

  async getPatient(patientId?: string): Promise<FhirPatient> {
    const id = patientId ?? this.session.patientId
    if (!id) {
      throw new FhirError('no patient in the launch context and none supplied')
    }
    return this.request<FhirPatient>(`Patient/${encodeURIComponent(id)}`)
  }

  /** One page of a search. Parameters are encoded here, so pass them raw. */
  async search<T = FhirResource>(
    resourceType: string,
    params: Record<string, string>,
  ): Promise<FhirBundle<T>> {
    const query = new URLSearchParams(params).toString()
    return this.request<FhirBundle<T>>(`${resourceType}?${query}`)
  }

  /**
   * Bone density observations for a patient, oldest first.
   *
   * Follows Bundle pagination. A patient with fifteen years of history can
   * exceed one page, and stopping at the first page would silently truncate
   * the earliest scans, which are exactly the ones the trajectory needs.
   */
  async getBoneDensityObservations(patientId?: string): Promise<FhirObservation[]> {
    const id = patientId ?? this.session.patientId
    if (!id) throw new FhirError('no patient in the launch context and none supplied')

    const codes = [
      LOINC.FEMORAL_NECK_BMD,
      LOINC.LUMBAR_SPINE_BMD,
      LOINC.TOTAL_HIP_BMD,
      LOINC.DXA_T_SCORE,
    ].join(',')

    const observations: FhirObservation[] = []
    let next: string | undefined =
      `Observation?patient=${encodeURIComponent(id)}&code=${codes}&_sort=date&_count=100`

    while (next) {
      const bundle: FhirBundle<FhirObservation> = await this.request<FhirBundle<FhirObservation>>(next)
      for (const entry of bundle.entry ?? []) observations.push(entry.resource)
      next = bundle.link?.find((link) => link.relation === 'next')?.url
    }

    return observations
  }

  async getVitals(patientId?: string): Promise<{ weightKg?: number; heightCm?: number }> {
    const id = patientId ?? this.session.patientId
    if (!id) throw new FhirError('no patient in the launch context and none supplied')

    const bundle = await this.request<FhirBundle<FhirObservation>>(
      `Observation?patient=${encodeURIComponent(id)}` +
        `&code=${LOINC.BODY_WEIGHT},${LOINC.BODY_HEIGHT}&_sort=-date&_count=10`,
    )

    let weightKg: number | undefined
    let heightCm: number | undefined

    for (const entry of bundle.entry ?? []) {
      const observation = entry.resource
      const code = observation.code.coding?.[0]?.code
      const quantity = observation.valueQuantity

      if (!quantity?.value) continue

      // Units vary by site. Converting on read means the FRAX call always
      // gets kilograms and centimetres regardless of what the EHR stores.
      if (code === LOINC.BODY_WEIGHT && weightKg === undefined) {
        const unit = quantity.unit?.toLowerCase() ?? 'kg'
        weightKg = unit.startsWith('lb') ? quantity.value * 0.45359237 : quantity.value
      }

      if (code === LOINC.BODY_HEIGHT && heightCm === undefined) {
        const unit = quantity.unit?.toLowerCase() ?? 'cm'
        if (unit.startsWith('in')) heightCm = quantity.value * 2.54
        else if (unit === 'm') heightCm = quantity.value * 100
        else heightCm = quantity.value
      }
    }

    return { weightKg, heightCm }
  }
}

export interface FhirResource {
  resourceType: string
  id?: string
}

// ---------------------------------------------------------------------------
// Post-launch verification
// ---------------------------------------------------------------------------

/**
 * What a verified launch looks like, with nothing in it that must not be
 * logged. No token, no name, no birth date. The patient id is the launch
 * context identifier and is included so a caller can decide for itself
 * whether its output channel may carry it.
 */
export interface LaunchVerification {
  patientId: string
  patient: { status: number; resourceType: string; idMatches: boolean }
  observations: {
    status: number
    resourceType: string
    bundleType?: string
    total?: number
    entries: number
    /** Distinct resource types in the entries, so a mixed bundle is visible. */
    resourceTypes: string[]
  }
}

/**
 * Prove the token can read: one Patient read, one Observation search.
 *
 * This is the smallest pair of requests that shows the whole chain worked:
 * the token is accepted, the patient scope covers the launch context, and
 * the search parameters are understood. An empty Observation bundle is a pass,
 * because sandbox patients routinely have no data in a given category, and
 * the point is the 200, not the contents.
 */
export async function verifyLaunchAccess(
  client: FhirClient,
  options: { category?: string; count?: number } = {},
): Promise<LaunchVerification> {
  const patientId = client.patientId
  if (!patientId) {
    throw new FhirError('token response carried no patient context, nothing to verify against')
  }

  const patient = await client.getPatient(patientId)
  if (patient.resourceType !== 'Patient') {
    throw new FhirError(`Patient read returned a ${patient.resourceType}, expected Patient`)
  }

  const bundle = await client.search<FhirResource>('Observation', {
    patient: patientId,
    category: options.category ?? 'vital-signs',
    _count: String(options.count ?? 5),
  })
  if (bundle.resourceType !== 'Bundle') {
    throw new FhirError(`Observation search returned a ${bundle.resourceType}, expected Bundle`)
  }

  const entries = bundle.entry ?? []
  const resourceTypes = Array.from(new Set(entries.map((entry) => entry.resource.resourceType))).sort()

  return {
    patientId,
    patient: { status: 200, resourceType: patient.resourceType, idMatches: patient.id === patientId },
    observations: {
      status: 200,
      resourceType: bundle.resourceType,
      bundleType: bundle.type,
      total: bundle.total,
      entries: entries.length,
      resourceTypes,
    },
  }
}

/** Age in years from a FHIR birthDate, used to build the FRAX payload. */
export function ageFromBirthDate(birthDate: string, asOf: Date = new Date()): number {
  const born = new Date(birthDate)
  let age = asOf.getFullYear() - born.getFullYear()
  const monthDelta = asOf.getMonth() - born.getMonth()
  if (monthDelta < 0 || (monthDelta === 0 && asOf.getDate() < born.getDate())) age -= 1
  return age
}
