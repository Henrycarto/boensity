import { NextRequest, NextResponse } from 'next/server'
import { completeSmartLaunch } from '@/lib/fhir'

/**
 * Authorization server redirect target.
 *
 * Verifies state, exchanges the code with the PKCE verifier, and stores the
 * session in an httpOnly cookie. The access token never reaches client
 * JavaScript, so an XSS on any page cannot lift it.
 */

export const dynamic = 'force-dynamic'

export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams
  const code = params.get('code')
  const state = params.get('state')
  const error = params.get('error')
  const errorDescription = params.get('error_description')

  const loginUrl = new URL('/login', request.nextUrl.origin)

  // The authorization server can decline, and a clinician cancelling consent
  // is a normal outcome rather than a fault.
  if (error) {
    loginUrl.searchParams.set('error', errorDescription ?? `Authorization declined: ${error}`)
    return NextResponse.redirect(loginUrl)
  }

  if (!code || !state) {
    loginUrl.searchParams.set('error', 'Authorization server returned no code or state.')
    return NextResponse.redirect(loginUrl)
  }

  try {
    const session = await completeSmartLaunch({ code, state })

    // With a patient in context, go straight to their trajectory. Making a
    // clinician who launched from a chart pick the same patient again is the
    // most common way an EHR integration wastes their time.
    if (session.patientId) {
      return NextResponse.redirect(
        new URL(`/patients/${encodeURIComponent(session.patientId)}/timeline`, request.nextUrl.origin),
      )
    }

    return NextResponse.redirect(new URL('/patients', request.nextUrl.origin))
  } catch (exception) {
    const message = exception instanceof Error ? exception.message : 'token exchange failed'
    loginUrl.searchParams.set('error', message)
    return NextResponse.redirect(loginUrl)
  }
}
