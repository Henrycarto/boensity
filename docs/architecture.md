# Architecture

## The shape of the problem

A patient's bone density history exists. It is just scattered. One scan sits in
a 2014 PDF on a radiology share, another in a 2019 fax from a different
hospital, a third in the current EHR. Each one was read once, reported as a
single number, and filed.

The clinical value is not in any one of those numbers. It is in the line
through them. Boensity exists to reconstruct that line and project it forward.

That splits cleanly into three problems that do not belong in one service:
getting the data out of the documents, modelling it, and tracking whether
anything was done about it.

## Services

### Boensity Ingest (port 8001)

Reads DEXA report PDFs and produces a normalised scan record.

Tries the PDF text layer first with pdfplumber. A large share of reports are
console exports that carry real text, and reading it is exact, free, and takes
milliseconds. Only documents with no usable text layer go to Google Cloud
Vision, which is approximate, billed per page, and slower.

Manufacturer detection runs both adapters and takes the stronger claim rather
than short-circuiting on the first keyword, because service reports and
comparison sheets mention competitor names in passing.

The part that matters is standardisation. See
[manufacturer-normalization.md](manufacturer-normalization.md).

Extraction is asynchronous through Celery when a document is uploaded, and
synchronous through `/extract` when a clinician is sitting on the review screen
waiting for the answer. Both paths call the same pipeline function so they
cannot drift apart and produce different numbers for one document.

Nothing reaches a patient timeline without a human confirming it.

### Boensity Timeline (port 8002)

Owns the longitudinal store and every number derived from it.

`trajectory_model.py` fits T-score against time with ordinary least squares.
The choice is deliberate: serial DEXA gives few points over many years, and a
higher-order fit would chase measurement noise and produce projections that
swing wildly on one new scan.

Every velocity claim is checked against the least significant change for the
site, which is 2.77 times the scanner precision error. A change inside that
band is noise, and reporting it as a trend would manufacture urgency. That is
the exact failure mode a clinical warning system cannot have.

`frax_calculator.py` computes the ten year fracture probability. See
[frax-methodology.md](frax-methodology.md).

`threshold_engine.py` turns the fitted line into events: where it crosses the
osteoporosis threshold, how confident that crossing is, and what should be
said about it.

### Boensity Sync (port 8003)

Tracks whether the treatment is being taken.

This service exists because the Timeline number is conditional. A FRAX
probability that drops from 24 percent to 19 percent on treatment is only true
if the patient takes the treatment, and roughly half of patients started on an
oral bisphosphonate have stopped within a year.

Scoring uses proportion of days covered, the measure payers and the adherence
literature already recognise. Implementing it openly matters: a pharmacy
director will not act on a score they cannot audit.

Two pieces are private. See [pharmacy-integration.md](pharmacy-integration.md).

## Data flow

```
DEXA PDF
   |
   v
[Ingest]  text layer?  -- yes --> pdfplumber
   |                   -- no  --> Cloud Vision OCR
   v
manufacturer detection -> vendor adapter -> raw extraction
   |
   v
schema_mapper: vendor BMD -> standardised BMD -> T-score vs one reference
   |
   v
review screen (human confirms or corrects)
   |
   v
[Timeline] POST /scans -> TimescaleDB hypertable
   |
   v
POST /trajectory -> fit, project, detect crossings, build alerts
POST /frax       -> ten year probability, untreated and treated
   |
   v
[Web] FractureRiskChart + RiskScoreBadge
   |
[Sync] pharmacy fill history -> adherence -> referral routing
```

## Why TimescaleDB

The per-patient dataset is tiny. A patient with fifteen years of history has
perhaps eight rows. Standard PostgreSQL handles that without effort, and if
the only query were "give me one patient's scans" there would be no argument
for an extension.

The query that decides the architecture is the other one. A department head
opening the dashboard asks "who in this cohort is trending toward the
threshold", which sweeps every patient across a time window. That is a
time-partitioned scan, and it is what chunk exclusion is built for.

Three specific features earn their place:

- **Hypertable partitioning on `scan_date`** with one year chunks. Scan
  intervals are measured in years, so a smaller chunk would produce thousands
  of near-empty chunks and slow planning down.
- **Continuous aggregates** for department scan volume, refreshed on a policy
  rather than recomputed per dashboard load.
- **Compression after five years.** Nothing is ever deleted, because the
  earliest scans are exactly the ones a fifteen year trajectory needs.
  Compression reclaims the space instead.

RDS carries the timescaledb extension, so all of this runs on managed
Postgres. Self-managing Timescale on EC2 would mean owning patching and
backups for a database holding patient data, which is the wrong thing to own.

## Response contract

Every endpoint on every service returns the same envelope, on success and on
failure:

```json
{
  "data": { },
  "error": { "code": "", "message": "", "field": null },
  "meta": { "service": "", "version": "", "generated_at": "" }
}
```

The frontend branches on one shape. Validation failures, upstream failures, and
unhandled exceptions all pass through handlers that preserve it, so a client
never has to guess whether a 422 body looks like a 200 body.

`meta` carries the context a number needs to be interpretable: the least
significant change used, the projection window, whether the FRAX figure
included a BMD measurement, whether pharmacy data was live or fixture.

## Degradation

Each service is useful with its external dependencies missing, and says so
rather than pretending otherwise.

| Missing | Effect | Reported at |
|---|---|---|
| TimescaleDB | Trajectory and FRAX still answer from the request body. Stored history is unavailable. | startup log |
| Cloud Vision | Text-native PDFs work. Scanned reports are rejected with a clear reason. | `/health` `ocr_available` |
| Pharmacy credentials | Sync serves recorded fixture responses, labelled as fixtures. | `/health` `live_pharmacy_connection` |
| Private scoring package | PDC, gaps, and banding still compute. The composite risk score is absent, not zero. | `/health` `private_scoring_available` |
| Private routing package | Candidates are returned in clinical eligibility order with no preselection. | `/health` `private_routing_available` |

The distinction between "absent" and "zero" is load bearing. A composite risk
score defaulting to zero would read as no risk.

## Security posture

- Patient documents are encrypted at rest with a customer managed KMS key.
- Presigned URLs for source documents expire in fifteen minutes, because those
  links end up in browser history and server logs.
- The data subnet has no default route. The database cannot reach the internet
  even if something in it is compromised.
- Security groups reference each other rather than CIDR ranges, so a task that
  is not a Boensity service cannot reach the database from inside the VPC.
- Exception text can carry document contents. It goes to the log, never to the
  response body.
- Postgres `log_statement` is set to `ddl`. Query parameters here are patient
  identifiers and clinical values, and logs are the easiest place to leak them.
- SMART on FHIR tokens live in httpOnly cookies. The PKCE code verifier never
  leaves the server.

## Frontend

Next.js App Router. The three service URLs are inlined at build time because
Next resolves `NEXT_PUBLIC_*` during the build, so setting them only at runtime
would ship a browser bundle pointing at the defaults.

The chart is D3 rather than a charting library. What it draws is not a line
chart with a threshold annotation added: the threshold band, the point where
the projection enters it, and the widening prediction interval are the content.
A library that gives you a line and a reference line would need fighting on
every one of them.

D3 rendering lives in `lib/d3-chart.ts` and knows nothing about React. The
component owns the DOM node, the resize observer, and the header state.

## Testing

155 tests across the three services.

The FRAX tests assert direction, ordering, and clinical plausibility rather
than exact equality against the certified tool, because this is an independent
implementation of the published methodology and pinning to Sheffield's decimals
would be a false claim. One test does pin: a calibration regression against the
published reference curve at eight ages, which is what catches an incidence
table edit that silently moves every patient in the system.

The adapter tests run end to end from real PDF bytes. Fixtures are generated
with reportlab so `pdf_parser` executes its real code path rather than a mock.

The demo fixtures the frontend falls back to are generated by calling the
actual engine, and CI fails if they are stale. A screenshot built from invented
numbers is worthless the moment someone technical asks how it was produced.
