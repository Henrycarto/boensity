import { describe, it } from 'node:test'
import assert from 'node:assert/strict'

import { buildAuthorizeUrl, type SmartConfiguration } from '../src/index'

const config: SmartConfiguration = {
  authorization_endpoint: 'https://auth.example.org/oauth2/authorize',
  token_endpoint: 'https://auth.example.org/oauth2/token',
}

const base = {
  iss: 'https://fhir.example.org/api/FHIR/R4',
  clientId: 'client-123',
  redirectUri: 'http://localhost:3000/api/fhir/callback',
  scope: 'launch/patient openid fhirUser patient/Patient.read',
  state: 'state-abc',
  codeChallenge: 'challenge-xyz',
}

describe('buildAuthorizeUrl', () => {
  it('sends every parameter a SMART v2 server requires for a standalone launch', () => {
    const url = new URL(buildAuthorizeUrl(config, base))

    assert.equal(url.origin + url.pathname, config.authorization_endpoint)
    assert.equal(url.searchParams.get('response_type'), 'code')
    assert.equal(url.searchParams.get('client_id'), base.clientId)
    assert.equal(url.searchParams.get('redirect_uri'), base.redirectUri)
    assert.equal(url.searchParams.get('scope'), base.scope)
    assert.equal(url.searchParams.get('state'), base.state)
    assert.equal(url.searchParams.get('aud'), base.iss)
    assert.equal(url.searchParams.get('code_challenge'), base.codeChallenge)
    assert.equal(url.searchParams.get('code_challenge_method'), 'S256')
    assert.equal(url.searchParams.has('launch'), false)
  })

  it('adds the launch token only for an EHR launch', () => {
    const url = new URL(buildAuthorizeUrl(config, { ...base, launch: 'opaque-launch' }))
    assert.equal(url.searchParams.get('launch'), 'opaque-launch')
  })

  it('keeps aud byte-identical to the issuer', () => {
    // Epic compares aud to the FHIR base as a string. Any encoding drift here
    // is an invalid_request with no useful message.
    const url = buildAuthorizeUrl(config, base)
    assert.ok(url.includes(`aud=${encodeURIComponent(base.iss)}`))
    assert.equal(new URL(url).searchParams.get('aud'), base.iss)
  })
})
