/**
 * SMART issuer profiles.
 *
 * A profile is the set of values that differ between authorization servers
 * and are fixed at registration time: which FHIR base to launch against, the
 * client id the server issued, the redirect it will accept, and the exact
 * scope list the app was registered with.
 *
 * The protocol itself does not change between profiles. Endpoints are always
 * discovered from the server, never hardcoded here, so a profile is only the
 * registration facts plus a default `iss` for standalone launch.
 *
 *   generic  the public SMART reference sandbox, or any server reachable with
 *            the FHIR_* variables. Accepts any client id, so it needs no
 *            registration and is the default.
 *
 *   epic     Epic's public FHIR R4 sandbox. Needs a client id from an app
 *            registered at open.epic.com, and the scopes requested must be
 *            the ones declared on that registration. Selected with
 *            SMART_ISSUER=epic.
 */

export type SmartIssuerName = 'generic' | 'epic'

export interface SmartIssuerProfile {
  name: SmartIssuerName
  /** Human readable, for the login screen and logs. */
  label: string
  /** FHIR base URL used as `iss` for standalone launch and as `aud`. */
  iss: string
  clientId: string
  /** Confidential clients only. Public clients (the default) leave it unset. */
  clientSecret?: string
  redirectUri: string
  /** Space separated, normalised: no duplicates, no stray whitespace. */
  scope: string
  /**
   * Environment variables the profile needs and did not find. Resolution
   * never throws so a page can still render and say what is missing; a
   * launch attempt refuses to start while this is non-empty.
   */
  missing: string[]
}

/** Any object shaped like process.env. Tests pass a plain record. */
export type SmartEnv = Record<string, string | undefined>

export const GENERIC_SANDBOX_FHIR_BASE = 'https://launch.smarthealthit.org/v/r4/fhir'
export const GENERIC_DEFAULT_SCOPES =
  'launch openid fhirUser offline_access patient/Patient.read patient/Observation.read'

export const EPIC_SANDBOX_FHIR_BASE = 'https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4'

/**
 * The scope list declared on the Epic app registration, in the order Epic
 * lists them. Epic rejects an authorize request whose scopes do not match the
 * registration, so this must be edited together with the registration and
 * never widened on its own. EPIC_SCOPES overrides it for a registration that
 * declared a different set.
 */
export const EPIC_DEFAULT_SCOPES =
  'launch launch/patient openid fhirUser patient/Patient.read patient/Observation.read offline_access'

const DEFAULT_REDIRECT_URI = 'http://localhost:3000/api/fhir/callback'

/** Collapse whitespace and drop duplicates while keeping the declared order. */
export function normaliseScopes(scope: string): string {
  const seen = new Set<string>()
  const ordered: string[] = []
  for (const entry of scope.split(/\s+/)) {
    if (!entry || seen.has(entry)) continue
    seen.add(entry)
    ordered.push(entry)
  }
  return ordered.join(' ')
}

/** `aud` must equal the FHIR base exactly, and a trailing slash breaks that. */
export function normaliseIss(iss: string): string {
  return iss.trim().replace(/\/+$/, '')
}

export function issuerNameFromEnv(env: SmartEnv): SmartIssuerName {
  const raw = (env.SMART_ISSUER ?? 'generic').trim().toLowerCase()
  if (raw === 'epic') return 'epic'
  if (raw === 'generic' || raw === '') return 'generic'
  throw new Error(`SMART_ISSUER must be "generic" or "epic", got "${env.SMART_ISSUER}"`)
}

export function resolveSmartIssuerProfile(env: SmartEnv): SmartIssuerProfile {
  const name = issuerNameFromEnv(env)

  if (name === 'epic') {
    const clientId = env.EPIC_CLIENT_ID?.trim() ?? ''
    return {
      name,
      label: 'Epic FHIR R4 sandbox',
      iss: normaliseIss(env.EPIC_FHIR_BASE_URL || EPIC_SANDBOX_FHIR_BASE),
      clientId,
      clientSecret: env.EPIC_CLIENT_SECRET || undefined,
      redirectUri: (env.EPIC_REDIRECT_URI || DEFAULT_REDIRECT_URI).trim(),
      scope: normaliseScopes(env.EPIC_SCOPES || EPIC_DEFAULT_SCOPES),
      missing: clientId ? [] : ['EPIC_CLIENT_ID'],
    }
  }

  return {
    name,
    label: 'SMART reference sandbox',
    iss: normaliseIss(env.FHIR_ISSUER || GENERIC_SANDBOX_FHIR_BASE),
    clientId: env.FHIR_CLIENT_ID?.trim() || 'boensity-local',
    clientSecret: env.FHIR_CLIENT_SECRET || undefined,
    redirectUri: (env.FHIR_REDIRECT_URI || DEFAULT_REDIRECT_URI).trim(),
    scope: normaliseScopes(env.FHIR_SCOPES || GENERIC_DEFAULT_SCOPES),
    missing: [],
  }
}
