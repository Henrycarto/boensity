import { afterEach, beforeEach, describe, it } from 'node:test'
import assert from 'node:assert/strict'

import {
  FhirClient,
  assertSmartConfigurationUsable,
  clearSmartConfigurationCache,
  discoverSmartConfiguration,
  verifyLaunchAccess,
  type SmartSession,
} from '../src/index'

const ISS = 'https://fhir.example.org/api/FHIR/R4'

const document = {
  authorization_endpoint: 'https://fhir.example.org/oauth2/authorize',
  token_endpoint: 'https://fhir.example.org/oauth2/token',
  code_challenge_methods_supported: ['S256'],
  capabilities: ['launch-standalone'],
}

type FetchLike = typeof globalThis.fetch

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('assertSmartConfigurationUsable', () => {
  it('accepts a document with both endpoints and S256', () => {
    assert.equal(assertSmartConfigurationUsable(document, ISS), document)
  })

  it('accepts a document that does not list challenge methods', () => {
    const { code_challenge_methods_supported: _omit, ...withoutMethods } = document
    assert.equal(assertSmartConfigurationUsable(withoutMethods, ISS), withoutMethods)
  })

  it('refuses a server that advertises PKCE without S256', () => {
    assert.throws(
      () =>
        assertSmartConfigurationUsable(
          { ...document, code_challenge_methods_supported: ['plain'] },
          ISS,
        ),
      /S256/,
    )
  })

  it('refuses relative or missing endpoints', () => {
    assert.throws(
      () => assertSmartConfigurationUsable({ ...document, token_endpoint: '/oauth2/token' }, ISS),
      /token_endpoint/,
    )
    assert.throws(
      () => assertSmartConfigurationUsable({ ...document, authorization_endpoint: '' }, ISS),
      /authorization_endpoint/,
    )
  })
})

describe('discoverSmartConfiguration', () => {
  const realFetch = globalThis.fetch
  let calls: string[] = []

  beforeEach(() => {
    calls = []
    clearSmartConfigurationCache()
    globalThis.fetch = (async (input: string | URL | Request) => {
      const url = String(input instanceof Request ? input.url : input)
      calls.push(url)
      if (url.endsWith('/.well-known/smart-configuration')) return jsonResponse(document)
      return jsonResponse({ resourceType: 'OperationOutcome' }, 404)
    }) as FetchLike
  })

  afterEach(() => {
    globalThis.fetch = realFetch
    clearSmartConfigurationCache()
  })

  it('fetches the well-known document once and serves it from cache after', async () => {
    const first = await discoverSmartConfiguration(ISS)
    const second = await discoverSmartConfiguration(`${ISS}/`)
    assert.equal(first, second)
    assert.deepEqual(calls, [`${ISS}/.well-known/smart-configuration`])
  })

  it('refetches when forced', async () => {
    await discoverSmartConfiguration(ISS)
    await discoverSmartConfiguration(ISS, { force: true })
    assert.equal(calls.length, 2)
  })

  it('does not cache a document it rejected', async () => {
    globalThis.fetch = (async () =>
      jsonResponse({ ...document, code_challenge_methods_supported: ['plain'] })) as FetchLike
    await assert.rejects(discoverSmartConfiguration(ISS), /S256/)
    globalThis.fetch = (async () => jsonResponse(document)) as FetchLike
    assert.equal((await discoverSmartConfiguration(ISS)).token_endpoint, document.token_endpoint)
  })
})

describe('verifyLaunchAccess', () => {
  const realFetch = globalThis.fetch
  let requests: Array<{ url: string; authorization: string | null }> = []

  const session: SmartSession = {
    accessToken: 'test-access-token',
    expiresAt: Date.now() + 60_000,
    scope: 'patient/Patient.read patient/Observation.read',
    iss: ISS,
    patientId: 'p-1',
  }

  beforeEach(() => {
    requests = []
    globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input instanceof Request ? input.url : input)
      const headers = new Headers(init?.headers)
      requests.push({ url, authorization: headers.get('Authorization') })
      if (url === `${ISS}/Patient/p-1`) {
        return jsonResponse({ resourceType: 'Patient', id: 'p-1', name: [{ family: 'Redacted' }] })
      }
      if (url.startsWith(`${ISS}/Observation?`)) {
        return jsonResponse({ resourceType: 'Bundle', type: 'searchset', total: 0 })
      }
      return jsonResponse({ resourceType: 'OperationOutcome' }, 404)
    }) as FetchLike
  })

  afterEach(() => {
    globalThis.fetch = realFetch
  })

  it('reads the patient and searches vital signs with the bearer token', async () => {
    const verified = await verifyLaunchAccess(new FhirClient(session))

    assert.deepEqual(
      requests.map((request) => request.url),
      [`${ISS}/Patient/p-1`, `${ISS}/Observation?patient=p-1&category=vital-signs&_count=5`],
    )
    assert.ok(requests.every((request) => request.authorization === 'Bearer test-access-token'))

    assert.equal(verified.patientId, 'p-1')
    assert.deepEqual(verified.patient, { status: 200, resourceType: 'Patient', idMatches: true })
    assert.equal(verified.observations.status, 200)
    assert.equal(verified.observations.entries, 0)
    assert.equal(verified.observations.total, 0)
    // Nothing from the Patient resource beyond its type and id survives.
    assert.equal(JSON.stringify(verified).includes('Redacted'), false)
  })

  it('fails when the token carried no patient context', async () => {
    const { patientId: _omit, ...withoutPatient } = session
    await assert.rejects(verifyLaunchAccess(new FhirClient(withoutPatient)), /no patient context/)
    assert.equal(requests.length, 0)
  })
})
