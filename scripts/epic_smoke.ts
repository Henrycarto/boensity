/**
 * Epic FHIR R4 sandbox smoke test: the full standalone launch, end to end.
 *
 *   npx tsx scripts/epic_smoke.ts
 *
 * Needs EPIC_CLIENT_ID and, if the registration used a different one,
 * EPIC_REDIRECT_URI. The script binds a listener on the redirect URI's host
 * and port, prints the authorize URL, and waits for the browser to come back.
 * Stop the Next dev server first if it is on the same port.
 *
 * What it proves, using the same client code the web app uses:
 *
 *   1. Epic's smart-configuration is discovered and passes validation.
 *   2. The authorize request is accepted and Epic redirects back with a code.
 *   3. The code exchanges for a token with the PKCE verifier.
 *   4. The token reads Patient/{patient} and searches Observation.
 *
 * Output is a redacted summary: patient id, resource types, counts. No token,
 * no code, no verifier, no name or birth date is printed. Exit status is 0 on
 * a full pass, 1 on any failure, 2 on a configuration problem.
 */

import http from 'node:http'

import {
  FhirClient,
  FhirError,
  buildAuthorizeUrl,
  deriveCodeChallenge,
  discoverSmartConfiguration,
  exchangeCodeForToken,
  generateCodeVerifier,
  generateState,
  resolveSmartIssuerProfile,
  sessionFromToken,
  verifyLaunchAccess,
} from '@boensity/fhir-client'

const TIMEOUT_SECONDS = Number(process.env.EPIC_SMOKE_TIMEOUT_SECONDS ?? 300)

function step(message: string): void {
  console.log(`[epic-smoke] ${message}`)
}

function fail(message: string, code: 1 | 2 = 1): never {
  console.error(`[epic-smoke] FAIL: ${message}`)
  process.exit(code)
}

interface Callback {
  code?: string
  state?: string
  error?: string
  errorDescription?: string
}

/**
 * Listen on the registered redirect URI until Epic sends the browser back.
 * The response page tells the person to return to the terminal; the code
 * itself is never echoed into the page.
 */
function waitForCallback(redirectUri: URL): Promise<Callback> {
  return new Promise((resolve, reject) => {
    const server = http.createServer((request, response) => {
      const url = new URL(request.url ?? '/', redirectUri.origin)

      if (url.pathname !== redirectUri.pathname) {
        response.writeHead(404, { 'Content-Type': 'text/plain' })
        response.end('not the SMART callback path\n')
        return
      }

      const result: Callback = {
        code: url.searchParams.get('code') ?? undefined,
        state: url.searchParams.get('state') ?? undefined,
        error: url.searchParams.get('error') ?? undefined,
        errorDescription: url.searchParams.get('error_description') ?? undefined,
      }

      response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' })
      response.end(
        '<!doctype html><title>Boensity Epic smoke</title>' +
          '<p style="font-family:system-ui;padding:2rem">Callback received. Return to the terminal.</p>',
      )

      clearTimeout(timer)
      server.close()
      resolve(result)
    })

    const timer = setTimeout(() => {
      server.close()
      reject(new Error(`no callback within ${TIMEOUT_SECONDS}s`))
    }, TIMEOUT_SECONDS * 1000)

    server.on('error', (error) => {
      clearTimeout(timer)
      reject(error)
    })

    const port = Number(redirectUri.port || (redirectUri.protocol === 'https:' ? 443 : 80))
    server.listen(port, redirectUri.hostname, () => {
      step(`listening on ${redirectUri.origin}${redirectUri.pathname}`)
    })
  })
}

async function main(): Promise<void> {
  const profile = resolveSmartIssuerProfile({ ...process.env, SMART_ISSUER: 'epic' })
  if (profile.missing.length > 0) {
    fail(`set ${profile.missing.join(', ')} before running (never commit it)`, 2)
  }

  const redirectUri = new URL(profile.redirectUri)
  if (redirectUri.protocol !== 'http:' && redirectUri.protocol !== 'https:') {
    fail(`EPIC_REDIRECT_URI must be an http(s) URL, got ${profile.redirectUri}`, 2)
  }
  if (redirectUri.protocol === 'https:') {
    fail('this script serves plain HTTP; register an http://localhost redirect URI for the smoke run', 2)
  }

  step(`issuer profile: ${profile.label}`)
  step(`fhir base (aud): ${profile.iss}`)
  step(`redirect uri: ${profile.redirectUri}`)
  step(`scopes requested: ${profile.scope}`)
  step(`client type: ${profile.clientSecret ? 'confidential' : 'public'} with PKCE S256`)

  // 1. Discovery, forced past the cache so the run reflects the live document.
  const config = await discoverSmartConfiguration(profile.iss, { force: true })
  step('discovery: OK')
  step(`  authorization_endpoint: ${config.authorization_endpoint}`)
  step(`  token_endpoint: ${config.token_endpoint}`)
  step(`  code_challenge_methods_supported: ${(config.code_challenge_methods_supported ?? []).join(', ')}`)
  step(`  capabilities: ${(config.capabilities ?? []).join(', ')}`)

  // 2. Authorize request. The verifier stays in this process.
  const codeVerifier = generateCodeVerifier()
  const state = generateState()
  const authorizeUrl = buildAuthorizeUrl(config, {
    iss: profile.iss,
    clientId: profile.clientId,
    redirectUri: profile.redirectUri,
    scope: profile.scope,
    state,
    codeChallenge: await deriveCodeChallenge(codeVerifier),
  })

  const pending = waitForCallback(redirectUri)

  console.log('')
  console.log('Open this URL in a browser, sign in with the Epic sandbox test credentials,')
  console.log('and pick a test patient:')
  console.log('')
  console.log(authorizeUrl)
  console.log('')

  const callback = await pending

  if (callback.error) {
    fail(`authorization server returned ${callback.error}: ${callback.errorDescription ?? 'no description'}`)
  }
  if (!callback.code || !callback.state) fail('callback carried no code or state')
  if (callback.state !== state) fail('state mismatch on the callback, refusing to exchange the code')
  step('callback: OK (state verified)')

  // 3. Token exchange with the PKCE verifier.
  const token = await exchangeCodeForToken({
    config,
    code: callback.code,
    redirectUri: profile.redirectUri,
    clientId: profile.clientId,
    clientSecret: profile.clientSecret,
    codeVerifier,
  })
  step('token exchange: OK')
  step(`  token_type: ${token.token_type}`)
  step(`  expires_in: ${token.expires_in}s`)
  step(`  scope granted: ${token.scope ?? '(none reported)'}`)
  step(`  patient in context: ${token.patient ? 'yes' : 'no'}`)
  step(`  id_token: ${token.id_token ? 'present' : 'absent'}`)
  step(`  refresh_token: ${token.refresh_token ? 'present' : 'absent'}`)
  step(`  fhirUser: ${token.fhirUser ? 'present' : 'absent'}`)

  if (!token.patient) {
    fail('no patient in the token response; launch/patient must be granted for the reads below')
  }

  // 4. Two real reads.
  const session = sessionFromToken(token, profile.iss)
  const verified = await verifyLaunchAccess(new FhirClient(session))

  step(`GET Patient/${verified.patientId}: ${verified.patient.status} ${verified.patient.resourceType}` +
    (verified.patient.idMatches ? '' : ' (id differs from launch context)'))
  step(
    `GET Observation?patient=${verified.patientId}&category=vital-signs&_count=5: ` +
      `${verified.observations.status} ${verified.observations.resourceType}` +
      ` type=${verified.observations.bundleType ?? '?'}` +
      ` entries=${verified.observations.entries}` +
      (verified.observations.total !== undefined ? ` total=${verified.observations.total}` : '') +
      (verified.observations.resourceTypes.length
        ? ` resourceTypes=${verified.observations.resourceTypes.join(',')}`
        : ''),
  )

  console.log('')
  console.log(
    JSON.stringify(
      {
        result: 'PASS',
        issuer: profile.iss,
        patientId: verified.patientId,
        patient: verified.patient,
        observations: verified.observations,
        grantedScopes: token.scope?.split(' ') ?? [],
        expiresIn: token.expires_in,
        idToken: Boolean(token.id_token),
        refreshToken: Boolean(token.refresh_token),
      },
      null,
      2,
    ),
  )
}

main().catch((error: unknown) => {
  if (error instanceof FhirError) {
    fail(`${error.message}${error.status ? ` (HTTP ${error.status})` : ''}`)
  }
  fail(error instanceof Error ? error.message : String(error))
})
