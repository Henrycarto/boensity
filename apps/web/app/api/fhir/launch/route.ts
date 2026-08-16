import { NextRequest, NextResponse } from 'next/server'
import { beginSmartLaunch } from '@/lib/fhir'

/**
 * Start a SMART on FHIR launch.
 *
 * Called two ways. An EHR calls it with iss and launch when a clinician opens
 * Boensity from inside a patient chart. The standalone form on /login calls it
 * with iss alone.
 *
 * Discovery and PKCE happen here rather than in the browser so the code
 * verifier stays server-side, which is what makes this a real public-client
 * flow rather than one with the secret in reach of any page script.
 */

export const dynamic = 'force-dynamic'

export async function GET(request: NextRequest) {
  const iss = request.nextUrl.searchParams.get('iss') ?? undefined
  const launch = request.nextUrl.searchParams.get('launch') ?? undefined

  try {
    const authorizeUrl = await beginSmartLaunch({ iss, launch })
    return NextResponse.redirect(authorizeUrl)
  } catch (error) {
    const message = error instanceof Error ? error.message : 'launch failed'
    const target = new URL('/login', request.nextUrl.origin)
    target.searchParams.set('error', `Could not start the SMART launch: ${message}`)
    if (iss) target.searchParams.set('iss', iss)
    return NextResponse.redirect(target)
  }
}
