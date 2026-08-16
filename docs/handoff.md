# Handoff

For whoever picks this up next, including me in six months.

Read [architecture.md](architecture.md) for how the system is shaped. This
document is the other half: where the risk is, which decisions look wrong and
are not, and what will break if you change the wrong thing.

## Current state in one paragraph

Three FastAPI services and a Next.js frontend. The clinical calculation layer
is finished and tested. The extraction layer works against the documented
vendor report layouts and has not met a real archive. Nothing has ever been
deployed: the Terraform is valid and unapplied, and the pharmacy integration is
one interface with a fixture driver behind it because there is no signed
network agreement. 155 tests pass, CI is green, and the demo cohort is five
synthetic patients whose numbers come from the real engine.

## Getting it running

```bash
cp .env.example .env
docker compose -f infra/docker-compose.yml up
```

Frontend on 3000, ingest 8001, timeline 8002, sync 8003. It starts without
Google Cloud Vision or pharmacy credentials and reports what is missing on each
service's `/health`.

For iterating on one service, run it directly. Each has its own venv and its
own `pytest.ini`:

```bash
cd services/timeline
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest
.venv/bin/uvicorn app.main:app --port 8002
```

**One trap that cost me time.** `next dev` and `next start` share the `.next`
directory. Running the dev server while a production server is up silently
overwrites the production build, and the running server then serves a broken
hybrid that fails at hydration with a minified React error. If you see
"Application error: a client-side exception has occurred" and the server
returned 200, this is almost certainly why. Rebuild before diagnosing anything
else.

## Where the risk actually is

Ranked by how likely it is to hurt you, not by how much code it is.

**1. The FRAX BMD centering.** `_MEAN_FEMORAL_NECK_T_SCORE` in
`frax_calculator.py` holds the population mean T-score per age and sex. The BMD
relative risk is measured from that mean, not from the young adult mean.

If you remove it, or "simplify" the BMD term to score against zero, every
patient in the system reads high and nothing will look obviously wrong. The
baseline incidence tables are population averages that already contain the
average bone density for that age group. An average 65 year old woman sits near
T -1.3. Scoring her against the young adult mean charges her for loss the
baseline already counts.

I made this exact mistake building it. A healthy 65 year old came out at 20
percent major osteoporotic against a published 11 percent. The guard is
`TestCalibration`, which pins output against published FRAX at eight ages. If
you touch anything in that module and that test goes red, the test is right.

**2. The standardisation coefficients.** `_STANDARDIZATION` in
`schema_mapper.py` is what makes a cross-scanner trajectory a measurement
rather than a drawing. Get a coefficient wrong and a patient's line develops a
step that looks like real bone loss.

Before clinical use these should be verified against a cross-calibration
phantom on the actual scanners in the deployment. The published regressions
carry 2 to 3 percent residual error, which is roughly 0.2 T-score units at the
femoral neck. That is why mixed-vendor series stay flagged all the way through
to the chart even after correction.

**3. The adapters meeting a real archive.** Both read the documented console
layouts and are tested end to end from generated PDF bytes. Real archives
contain variants: older firmware, different locales, faxed copies with the
letterhead missing. Each variant is a small addition rather than a rewrite, but
expect to add several in the first week against real data.

**4. OCR accuracy is unmeasured.** `vision_client.py` implements both Vision
paths correctly and cleans up its scratch objects. It has never been run
against a corpus of real scanned faxes. Do not assume the confidence numbers
coming out of it are calibrated until you have checked them against known
values.

## Decisions that look wrong and are not

Someone will try to "fix" each of these. Read this first.

**A patient losing 0.05 T-score units per year is reported as stable.** That is
the least significant change check in `trajectory_model.py`. Densitometry has
real precision error, and a change smaller than 2.77 times it cannot be
distinguished from scanner noise. Reporting it as a trend would manufacture
urgency. A warning system that cries wolf stops being believed, and a warning
nobody believes is worse than no warning.

**Two scans produce no R squared and no confidence interval.** Two points
always fit a line perfectly. Reporting R squared of 1.0 would imply a certainty
the data does not contain.

**`risk_score` comes back `None`, not `0`.** The composite adherence weighting
is in a private package that is not in this repository. Absent and zero are
different claims, and zero reads as no risk. The same pattern governs
`routing_available` in `referral_router.py`. If you find yourself adding
`or 0` to either, stop.

**The vendor T-score and the standardised T-score are both stored.** They are
not redundant. The vendor value is what is printed on the report the patient
may be holding, and the standardised value is the only one valid to plot across
scanners. The review screen shows both on purpose.

**No pharmacy is preselected when the private ranking package is missing.**
Returning the first eligible candidate would present list order as a
recommendation, and would pass a commercial judgement off as a clinical one.

**The chart is D3 rather than a charting library.** The threshold band, the
crossing point, and the widening prediction interval are the content, not
decoration. Swapping in a library means fighting it on all three.

**Ordinary least squares, not something fancier.** Serial DEXA gives few points
over many years. A higher-order fit chases measurement noise and produces
projections that swing wildly on one new scan.

## Before you touch these

**The FRAX incidence tables.** They are European reference rates. FRAX is
calibrated per country because fracture incidence varies more than tenfold
worldwide. Deploying outside Europe means replacing them, and that is the
single most important change before clinical use. They sit at the top of the
file rather than in a config for exactly this reason. Re-run
`TestCalibration` after any edit, and update the reference table in
`frax-methodology.md` if you intentionally recalibrate.

**Anything in `trajectory_model.py`, `frax_calculator.py`, or
`threshold_engine.py`.** The demo fixtures the frontend falls back to are
generated from these. CI fails if they drift. Regenerate with:

```bash
services/timeline/.venv/bin/python scripts/generate_demo_fixtures.py
services/sync/.venv/bin/python scripts/generate_sync_fixtures.py
```

**The site label maps in either adapter.** Two specific traps. On Hologic,
"Total" means the hip composite on a hip table and L1-L4 on a spine table, so
the table context decides; getting it backwards files a spine value as a hip
value and the two have different diagnostic thresholds. On GE Lunar, "Upper
Neck" is a distinct region that is not the femoral neck, and it is kept out by
longest-match lookup. Shortest-match would fold it into the neck slot.

**`EXPECTED_COVERAGE_DAYS` in `adherence_scorer.py`.** Pharmacy feeds record a
six month denosumab injection with `days_supply` of 1. Taking that literally
scores a perfectly adherent patient at 0.5 percent coverage. This is the most
damaging single bug available in that module.

## What is deliberately absent

Two components live in a private package, `boensity_sync_private`, which is not
committed:

- the composite adherence risk weighting behind `compute_risk_score`
- the pharmacy ranking and commission model behind `rank_candidates`

Both are imported behind a try block. The public code degrades and reports the
degradation rather than substituting a default. See
[pharmacy-integration.md](pharmacy-integration.md) for why the split falls
where it does.

CI has a check that fails if that package is ever committed.

## What is unproven

Be honest about this with anyone who asks.

| Area | State |
|---|---|
| Terraform | Valid and formatted, never applied. No plan reviewed against real quotas. |
| Deploy workflow | Never run past the AWS credentials step. |
| Migration task | The inline Python in `deploy.yml` is the least tested thing in the repo. |
| TimescaleDB query plans | Schema is valid, never run against a cohort large enough to matter. |
| OCR | Never run against real scanned documents. |
| Pharmacy integration | Fixture driver only. |

The Docker images are now built on arm64 in CI on every push, so the
Dockerfiles and the Graviton cross-build are covered. That was added
specifically because the deploy workflow could not exercise them.

## Knowing you have not broken it

```bash
# each service
cd services/<name> && .venv/bin/ruff check app tests \
  && .venv/bin/ruff format --check app tests \
  && .venv/bin/pytest

# frontend
npm run typecheck && npm run lint && npm run build

# terraform, if you do not have it installed locally
docker run --rm -v "$PWD/infra/terraform:/work" -w /work \
  hashicorp/terraform:1.9.5 fmt -check -recursive
```

CI enforces all of the above plus three hygiene checks: no PDFs or agent files
committed, no em dashes anywhere, and no private package. The em dash check
builds the character with `printf` rather than writing it literally, because
otherwise the workflow file matches its own check and can never pass.

## Two footguns in the tooling

**A `*/` inside a TypeScript block comment closes it.** Writing a file path
like `services/*/app/schemas.py` in a docstring breaks the parse with a cascade
of unrelated syntax errors. Cost me a confusing typecheck failure.

**Action version tags are not uniform.** `aquasecurity/trivy-action` uses
`v`-prefixed tags. Pinning a bare version fails the job at setup, before any
step runs, which looks nothing like a configuration error and reads as a
mysterious 2 second failure.

## Open questions for whoever continues

- **Patient identity.** The platform currently receives a patient context per
  request rather than owning a directory. Whether it should own one, or always
  defer to the EHR, is a product decision that has not been made.
- **Audit trail.** There is no record of who viewed which chart. That is
  required before this goes anywhere near real patients.
- **FRAX licensing.** The intended path is routing the point estimate through a
  licensed FRAX API and using this implementation for the projection. That
  conversation with Sheffield has not happened.
- **Which country's incidence tables ship first.** This gates any real
  deployment and depends on where the first customer is.
