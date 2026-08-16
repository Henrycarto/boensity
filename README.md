# Boensity

Predictive bone density trajectory platform.

A DEXA report tells a physician where a patient is today. It has never told
them how fast they are moving. Two patients can both sit at T -1.8 and have
completely different futures: one has been flat for six years, the other lost
0.9 T-score units in four. Static reporting treats them identically.

Boensity reads a patient's scan history out of the PDFs it is already scattered
across, normalises the values so scans from different manufacturers sit on one
axis, models the line between them, and projects the ten year fracture
probability forward using the WHO FRAX input variables.

## What it does

**Ingest** reads DEXA report PDFs from Hologic and GE Lunar consoles. It pulls
T-scores, BMD, scan dates, and scanner metadata, then converts the values to
standardised BMD and rescores them against a single reference database. Without
that step, a series spanning a scanner replacement shows a step change that is
an artefact of the hardware rather than the patient.

**Timeline** stores the longitudinal history in TimescaleDB, fits the rate of
decline against scanner precision error, projects it forward, and computes the
ten year probability of major osteoporotic and hip fracture. It reports where
the projected line crosses the osteoporosis threshold and how much that
crossing can be trusted.

**Sync** tracks whether the treatment is actually being taken. A projected risk
reduction only holds if the patient fills the prescription, and roughly half
stop within a year. It scores adherence, flags supply gaps, and routes
referrals to pharmacies that can handle the specific drug.

## Running it

```bash
cp .env.example .env
docker compose -f infra/docker-compose.yml up
```

That brings up TimescaleDB, Redis, MinIO, the three services, a Celery worker,
and the frontend on http://localhost:3000.

It starts without Google Cloud Vision or pharmacy credentials. Ingest handles
text-native PDFs and rejects scanned ones with a clear message. Sync runs its
fixture driver. Both report the degraded capability on `/health` rather than
pretending to be fully wired.

### Without Docker

```bash
npm install
npm run dev                                    # frontend on :3000

cd services/timeline
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8002     # same shape for ingest and sync
```

### Tests

```bash
cd services/timeline && .venv/bin/pytest      # 81 tests
cd services/ingest   && .venv/bin/pytest      # 38 tests
cd services/sync     && .venv/bin/pytest      # 36 tests
npm run typecheck && npm run lint && npm run build
```

## Layout

```
apps/web            Next.js 14 App Router frontend
services/ingest     PDF extraction and cross-manufacturer normalisation
services/timeline   Trajectory modelling and FRAX
services/sync       Adherence and pharmacy referral
packages            Shared types and the SMART on FHIR client
infra               docker-compose and Terraform
docs                Architecture and methodology
scripts             Demo fixture generators
```

## Documentation

- [Architecture](docs/architecture.md), including why TimescaleDB and how the
  services degrade when their dependencies are missing.
- [FRAX methodology](docs/frax-methodology.md), including what this
  implementation is and is not, and its calibration against published values.
- [Manufacturer normalization](docs/manufacturer-normalization.md), the
  standardisation that makes a cross-scanner trajectory valid.
- [Pharmacy integration](docs/pharmacy-integration.md), adherence scoring and
  the public and private split in referral routing.

## Build status

Stated honestly, because a demo presented as a product wastes everyone's time
in the first technical conversation.

### Production ready

The clinical calculation layer. It is tested, calibrated, and its limits are
documented and surfaced in the API.

- Trajectory fitting, velocity with confidence intervals, and forward
  projection with prediction intervals.
- Least significant change handling, so a change inside the scanner noise floor
  is never reported as a trend.
- FRAX ten year probability with competing mortality, calibrated to within 1.1
  percentage points of published values across ages 50 to 85, with a regression
  test guarding it.
- Threshold crossing detection with graded confidence.
- Cross-manufacturer BMD standardisation using the published ICSBM regressions.
- Hologic and GE Lunar adapters, tested end to end from real PDF bytes.
- The `{data, error, meta}` contract, preserved through every error path.
- SMART on FHIR OAuth 2.0 with PKCE, both EHR and standalone launch, tokens in
  httpOnly cookies.
- The FractureRiskChart, RiskScoreBadge, AdherencePanel, and the ingest review
  screen.

### Demo ready

Works and is not yet proven at scale or against real-world input variety.

- Cloud Vision OCR. The client handles both the synchronous and asynchronous
  paths correctly and cleans up its scratch objects, but it has been exercised
  against generated fixtures rather than a corpus of real scanned faxes. OCR
  accuracy on degraded documents is unmeasured.
- The adapters read the documented console layouts. Real archives contain
  variants, and each new one is a small addition rather than a rewrite.
- TimescaleDB schema, hypertable, continuous aggregates, and compression
  policies are written and valid. They have not been run against a cohort large
  enough to prove the query plans.
- Terraform is written and CI validates syntax and formatting. It has not been
  applied to a live AWS account, so no plan has been reviewed against real
  resource limits or quotas.
- The demo cohort is five synthetic patients. Their numbers are computed by the
  real engine, not written by hand, and CI fails if the fixtures drift from the
  code. They are still five patients.

### Scaffolded

Structure exists, the integration is not built.

- Patient record management. Boensity currently receives a patient context per
  request rather than owning a patient directory.
- Authentication beyond the SMART launch. There is no local user store, no
  roles, and no audit trail of who viewed which chart.
- The pharmacy portal client has one interface and a fixture driver. Real
  network integration needs a signed agreement before it can be built against.

### Requires live pharmacy API credentials

Nothing here is written and waiting to be switched on. These are integration
points that need a partner first.

- Real fill history in place of fixtures.
- Referral submission reaching an actual pharmacy.
- Referral acceptance and decline callbacks.
- Dispensing confirmation, closing the loop between a referral and a fill.

Two components are also deliberately absent from this repository: the composite
adherence risk weighting in `adherence_scorer.py` and the pharmacy ranking and
commission model in `referral_router.py`. Both live in a private package. The
code imports them behind a try block and reports `risk_score_available` or
`routing_available` as false when they are missing, so everything else keeps
working and nothing invents a number in their place.

## Clinical status

Clinical decision support. Not a diagnostic device, and not certified.

Every projection is a model of past measurements, not a prediction of an
individual outcome. The FRAX implementation reproduces published methodology
and is not the certified University of Sheffield tool; any regulated deployment
routes the point estimate through a licensed FRAX API and uses this
implementation for the trajectory projection.

No real patient data is in this repository. The demo cohort is synthetic, and
`.gitignore` excludes PDFs and the uploads directory.
