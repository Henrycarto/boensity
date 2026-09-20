/**
 * Epic FHIR R4 sandbox integration test.
 *
 * Runs only when EPIC_CLIENT_ID is set, which is the pytest `-m epic` idea
 * expressed in node:test. It talks to the real sandbox over the network and
 * proves two things without a browser:
 *
 *   1. Epic's smart-configuration document is what this client needs: both
 *      endpoints, PKCE S256, and at least one launch capability.
 *   2. The authorize request this client builds for that document carries
 *      every parameter Epic checks, with aud equal to the FHIR base.
 *
 * It cannot log in. The full round trip (authorize, callback, token exchange,
 * Patient read, Observation search) is verified interactively with
 * scripts/epic_smoke.ts, which uses the same client code.
 */

import { describe, it } from 'node:test'
import assert from 'node:assert/strict'

import {
  EPIC_SANDBOX_FHIR_BASE,
  buildAuthorizeUrl,
  deriveCodeChallenge,
  discoverSmartConfiguration,
  generateCodeVerifier,
  generateState,
  resolveSmartIssuerProfile,
} from '../src/index'

const clientId = process.env.EPIC_CLIENT_ID?.trim()

describe('epic sandbox', { skip: clientId ? false : 'EPIC_CLIENT_ID is not set' }, () => {
  const profile = resolveSmartIssuerProfile({ ...process.env, SMART_ISSUER: 'epic' })

  it('targets the Epic sandbox FHIR base by default', () => {
    if (!process.env.EPIC_FHIR_BASE_URL) assert.equal(profile.iss, EPIC_SANDBOX_FHIR_BASE)
    assert.match(profile.iss, /^https:\/\//)
    assert.deepEqual(profile.missing, [])
  })

  it('serves a smart-configuration this client can use', async () => {
    const config = await discoverSmartConfiguration(profile.iss, { force: true })

    assert.match(config.authorization_endpoint, /^https:\/\//)
    assert.match(config.token_endpoint, /^https:\/\//)
    assert.ok(
      config.code_challenge_methods_supported?.includes('S256'),
      'Epic must advertise PKCE S256',
    )
    const capabilities = config.capabilities ?? []
    assert.ok(
      capabilities.includes('launch-ehr') || capabilities.includes('launch-standalone'),
      `expected a launch capability, got ${capabilities.join(', ')}`,
    )
  })

  it('builds an authorize request with every parameter Epic checks', async () => {
    const config = await discoverSmartConfiguration(profile.iss)
    const verifier = generateCodeVerifier()
    const state = generateState()

    const url = new URL(
      buildAuthorizeUrl(config, {
        iss: profile.iss,
        clientId: profile.clientId,
        redirectUri: profile.redirectUri,
        scope: profile.scope,
        state,
        codeChallenge: await deriveCodeChallenge(verifier),
      }),
    )

    assert.equal(url.origin + url.pathname, config.authorization_endpoint)
    assert.equal(url.searchParams.get('response_type'), 'code')
    assert.equal(url.searchParams.get('client_id'), clientId)
    assert.equal(url.searchParams.get('redirect_uri'), profile.redirectUri)
    assert.equal(url.searchParams.get('scope'), profile.scope)
    assert.equal(url.searchParams.get('state'), state)
    assert.equal(url.searchParams.get('aud'), profile.iss)
    assert.equal(url.searchParams.get('code_challenge_method'), 'S256')
    assert.equal(url.searchParams.get('code_challenge'), await deriveCodeChallenge(verifier))
    assert.equal(url.searchParams.has('launch'), false, 'standalone launch sends no launch token')

    // The registration declared these. Requesting anything else is the
    // 400 Epic returns with no explanation.
    for (const scope of ['openid', 'fhirUser', 'patient/Patient.read', 'patient/Observation.read']) {
      assert.ok(profile.scope.split(' ').includes(scope), `scope list must include ${scope}`)
    }
  })

  it('adds the launch token for an EHR launch and nothing else changes', async () => {
    const config = await discoverSmartConfiguration(profile.iss)
    const shared = {
      iss: profile.iss,
      clientId: profile.clientId,
      redirectUri: profile.redirectUri,
      scope: profile.scope,
      state: 's',
      codeChallenge: 'c',
    }
    const standalone = new URL(buildAuthorizeUrl(config, shared))
    const ehr = new URL(buildAuthorizeUrl(config, { ...shared, launch: 'launch-token' }))

    assert.equal(ehr.searchParams.get('launch'), 'launch-token')
    ehr.searchParams.delete('launch')
    assert.equal(ehr.toString(), standalone.toString())
  })
})
