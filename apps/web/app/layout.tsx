import type { Metadata } from 'next'
import { DM_Mono, Inter } from 'next/font/google'
import Link from 'next/link'
import './globals.css'

// Inter for interface text, DM Mono for every clinical value. The split is not
// decorative: mono on T-scores and probabilities makes a column of numbers
// align on the decimal and signals that the value is measured rather than
// written.
const inter = Inter({
  subsets: ['latin'],
  variable: '--font-inter',
  display: 'swap',
})

const dmMono = DM_Mono({
  subsets: ['latin'],
  weight: ['400', '500'],
  variable: '--font-dm-mono',
  display: 'swap',
})

export const metadata: Metadata = {
  title: 'Boensity',
  description:
    'Predictive bone density trajectory platform. Projects fracture risk from serial DEXA scans.',
  robots: { index: false, follow: false },
}

const NAV = [
  { href: '/dashboard', label: 'Dashboard' },
  { href: '/patients', label: 'Patients' },
  { href: '/ingest', label: 'Ingest' },
]

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${dmMono.variable}`}>
      <body className="min-h-screen bg-base text-ink">
        <div className="flex min-h-screen flex-col">
          <header className="border-b border-line">
            <div className="mx-auto flex max-w-[1600px] items-center justify-between px-6 py-3">
              <div className="flex items-baseline gap-8">
                <Link href="/" className="flex items-baseline gap-2">
                  {/* A mark, not a logo. The square is the danger colour so the
                      product's one signal is present from the first pixel. */}
                  <span
                    className="inline-block h-[10px] w-[10px]"
                    style={{ backgroundColor: '#E05C2A' }}
                    aria-hidden
                  />
                  <span className="text-sm font-medium tracking-tight text-ink">Boensity</span>
                </Link>

                <nav className="flex items-baseline gap-6">
                  {NAV.map((item) => (
                    <Link
                      key={item.href}
                      href={item.href}
                      className="text-xs text-ink-muted transition-colors hover:text-ink"
                    >
                      {item.label}
                    </Link>
                  ))}
                </nav>
              </div>

              <Link
                href="/login"
                className="text-xs text-ink-muted transition-colors hover:text-ink"
              >
                Connect EHR
              </Link>
            </div>
          </header>

          <main className="flex-1">{children}</main>

          <footer className="border-t border-line">
            <div className="mx-auto flex max-w-[1600px] flex-wrap items-center justify-between gap-3 px-6 py-4">
              <p className="text-[11px] text-ink-faint">
                Clinical decision support. Not a diagnostic device. Every projection is a model of
                past measurements, not a prediction of an individual outcome.
              </p>
              <p className="mono text-[11px] text-ink-faint">v0.1.0</p>
            </div>
          </footer>
        </div>
      </body>
    </html>
  )
}
