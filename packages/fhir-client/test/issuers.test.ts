import { describe, it } from 'node:test'
import assert from 'node:assert/strict'

import {
  EPIC_DEFAULT_SCOPES,
  EPIC_SANDBOX_FHIR_BASE,
  GENERIC_SANDBOX_FHIR_BASE,
  normaliseIss,
  normaliseScopes,
  resolveSmartIssuerProfile,
} from '../src/index'

describe('issuer profiles', () => {
  it('defaults to the generic reference sandbox with no environment', () => {
    const profile = resolveSmartIssuerProfile({})
    assert.equal(profile.name, 'generic')
    assert.equal(profile.iss, GENERIC_SANDBOX_FHIR_BASE)
    assert.equal(profile.clientId, 'boensity-local')
    assert.deepEqual(profile.missing, [])
  })

  it('keeps the generic profile driven by the FHIR_* variables', () => {
    const profile = resolveSmartIssuerProfile({
      FHIR_ISSUER: 'https://fhir.hospital.example/r4/',
      FHIR_CLIENT_ID: 'hospital-client',
      FHIR_REDIRECT_URI: 'https://boensity.example/api/fhir/callback',
      FHIR_SCOPES: 'launch openid',
    })
    assert.equal(profile.iss, 'https://fhir.hospital.example/r4')
    assert.equal(profile.clientId, 'hospital-client')
    assert.equal(profile.redirectUri, 'https://boensity.example/api/fhir/callback')
    assert.equal(profile.scope, 'launch openid')
  })

  it('selects Epic with SMART_ISSUER=epic and reads only EPIC_* variables', () => {
    const profile = resolveSmartIssuerProfile({
      SMART_ISSUER: 'epic',
      EPIC_CLIENT_ID: 'epic-non-prod-id',
      EPIC_REDIRECT_URI: 'http://localhost:3000/api/fhir/callback',
      FHIR_CLIENT_ID: 'must-not-leak-into-epic',
    })
    assert.equal(profile.name, 'epic')
    assert.equal(profile.iss, EPIC_SANDBOX_FHIR_BASE)
    assert.equal(profile.clientId, 'epic-non-prod-id')
    assert.equal(profile.clientSecret, undefined)
    assert.equal(profile.scope, EPIC_DEFAULT_SCOPES)
    assert.deepEqual(profile.missing, [])
  })

  it('reports a missing Epic client id instead of inventing one', () => {
    const profile = resolveSmartIssuerProfile({ SMART_ISSUER: 'epic' })
    assert.equal(profile.clientId, '')
    assert.deepEqual(profile.missing, ['EPIC_CLIENT_ID'])
  })

  it('lets EPIC_SCOPES replace the declared list for a different registration', () => {
    const profile = resolveSmartIssuerProfile({
      SMART_ISSUER: 'epic',
      EPIC_CLIENT_ID: 'x',
      EPIC_SCOPES: '  launch/patient   openid  patient/Patient.read openid ',
    })
    assert.equal(profile.scope, 'launch/patient openid patient/Patient.read')
  })

  it('declares the scopes registered on the Epic app, in that order', () => {
    assert.equal(
      EPIC_DEFAULT_SCOPES,
      'launch launch/patient openid fhirUser patient/Patient.read patient/Observation.read offline_access',
    )
  })

  it('rejects an unknown issuer name', () => {
    assert.throws(() => resolveSmartIssuerProfile({ SMART_ISSUER: 'cerner' }), /SMART_ISSUER/)
  })

  it('normalises scopes and issuers', () => {
    assert.equal(normaliseScopes(' a  b\ta a '), 'a b')
    assert.equal(normaliseIss(' https://x.example/fhir// '), 'https://x.example/fhir')
  })
})
