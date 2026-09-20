import { describe, it } from 'node:test'
import assert from 'node:assert/strict'

import { deriveCodeChallenge, generateCodeVerifier, generateState } from '../src/index'

const BASE64URL = /^[A-Za-z0-9_-]+$/

describe('PKCE', () => {
  it('derives the S256 challenge from RFC 7636 appendix B', async () => {
    // The worked example in the RFC. If this ever changes, every
    // authorization server will reject the exchange.
    const verifier = 'dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk'
    assert.equal(await deriveCodeChallenge(verifier), 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM')
  })

  it('generates a base64url verifier of 32 random bytes', () => {
    const verifier = generateCodeVerifier()
    // 32 bytes encode to 43 characters without padding, inside the 43..128
    // range RFC 7636 section 4.1 requires.
    assert.equal(verifier.length, 43)
    assert.match(verifier, BASE64URL)
    assert.notEqual(verifier, generateCodeVerifier())
  })

  it('generates an unpredictable base64url state', () => {
    const state = generateState()
    assert.equal(state.length, 22)
    assert.match(state, BASE64URL)
    assert.notEqual(state, generateState())
  })
})
