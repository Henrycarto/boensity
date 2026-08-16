import { readSmartSession, fhirConfig } from '@/lib/fhir'

/**
 * EHR connection screen.
 *
 * There is no Boensity password. Authentication is SMART on FHIR against the
 * hospital's own authorization server, which is the only model a hospital IT
 * department will accept: no second credential store, no separate account
 * lifecycle, and access that ends when the clinician's EHR access ends.
 */
export default function LoginPage({
  searchParams,
}: {
  searchParams: { error?: string; iss?: string; launch?: string }
}) {
  const session = readSmartSession()
  const config = fhirConfig()

  return (
    <div className="mx-auto flex max-w-[720px] flex-col justify-center px-6 py-16">
      <h1 className="text-lg tracking-tight text-ink">Connect an EHR</h1>
      <p className="mt-2 max-w-lg text-xs leading-relaxed text-ink-muted">
        Boensity authenticates through SMART on FHIR. Sign in against your hospital&apos;s FHIR
        server and the app reads bone density observations for the patient in context. No separate
        Boensity account is created.
      </p>

      {searchParams.error && (
        <div
          className="mt-6 border px-4 py-3 text-xs"
          style={{
            borderColor: '#E05C2A',
            color: '#E05C2A',
            backgroundColor: 'rgba(224,92,42,0.06)',
          }}
        >
          {searchParams.error}
        </div>
      )}

      {session ? (
        <section className="panel mt-8">
          <div className="panel-header">
            <h2 className="label">Connected</h2>
            <span
              className="border px-2 py-[2px] text-[10px] uppercase tracking-[0.1em]"
              style={{ borderColor: '#238636', color: '#238636' }}
            >
              Active
            </span>
          </div>

          <dl className="grid grid-cols-2 gap-x-6 gap-y-4 px-4 py-4">
            <Field label="FHIR server" value={session.iss} />
            <Field label="Patient in context" value={session.patientId ?? 'None'} />
            <Field label="User" value={session.fhirUser ?? 'Not disclosed'} />
            <Field
              label="Token expires"
              value={new Date(session.expiresAt).toLocaleTimeString('en-GB')}
            />
          </dl>

          <div className="border-t border-line px-4 py-3">
            <p className="text-[11px] leading-relaxed text-ink-faint">
              Granted scopes: <span className="mono">{session.scope}</span>
            </p>
          </div>

          <form action="/api/fhir/logout" method="post" className="border-t border-line px-4 py-3">
            <button
              type="submit"
              className="border border-line-strong px-4 py-2 text-sm text-ink transition-colors hover:border-ink-muted"
            >
              Disconnect
            </button>
          </form>
        </section>
      ) : (
        <section className="panel mt-8">
          <div className="panel-header">
            <h2 className="label">Standalone launch</h2>
          </div>

          {/* GET to the launch route, which performs discovery and PKCE
              server-side and redirects to the authorization server. The code
              verifier never reaches the browser. */}
          <form action="/api/fhir/launch" method="get" className="px-4 py-4">
            <label htmlFor="iss" className="text-[11px] uppercase tracking-[0.14em] text-ink-faint">
              FHIR server base URL
            </label>
            <input
              id="iss"
              name="iss"
              type="url"
              required
              defaultValue={searchParams.iss ?? config.defaultIss}
              className="mono mt-2 w-full border border-line bg-base px-3 py-2 text-sm text-ink"
            />

            <p className="mt-2 text-[11px] leading-relaxed text-ink-faint">
              The default points at the public SMART sandbox, which is the fastest way to see the
              full flow without hospital credentials.
            </p>

            <button
              type="submit"
              className="mt-4 border border-danger bg-danger px-4 py-2 text-sm font-medium text-[#0D1117] transition-colors hover:bg-[#F06A36]"
            >
              Authorize
            </button>
          </form>

          <div className="border-t border-line px-4 py-3">
            <p className="text-[11px] leading-relaxed text-ink-faint">
              EHR launch is also supported. When a clinician opens Boensity from inside a patient
              chart, the EHR calls{' '}
              <span className="mono">/api/fhir/launch?iss=...&amp;launch=...</span> and the patient
              arrives with the token, so no patient picker is shown.
            </p>
          </div>
        </section>
      )}
    </div>
  )
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] uppercase tracking-[0.14em] text-ink-faint">{label}</dt>
      <dd className="mono mt-1 truncate text-xs text-ink" title={value}>
        {value}
      </dd>
    </div>
  )
}
