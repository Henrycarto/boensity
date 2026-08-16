import { NextRequest, NextResponse } from 'next/server'
import { endSmartSession } from '@/lib/fhir'

/**
 * Clear the SMART session.
 *
 * POST rather than GET so a prefetch or an image tag cannot sign a clinician
 * out mid-consultation.
 */

export const dynamic = 'force-dynamic'

export async function POST(request: NextRequest) {
  endSmartSession()
  return NextResponse.redirect(new URL('/login', request.nextUrl.origin), { status: 303 })
}
