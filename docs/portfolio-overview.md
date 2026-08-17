# Boensity

Predictive bone density trajectory platform. Case file.

## Product thesis

Osteoporosis is diagnosed late for a structural reason, not a clinical one. A
DEXA scan produces a T-score, the report states which of three bands that score
falls in, and the patient is told to come back in two years. The measurement is
accurate and the reporting format throws away most of its value: it describes a
position and says nothing about a direction. Two women can both be reported as
"osteopenia, repeat in two years" when one has been stable for six years and
the other has lost 0.9 T-score units in four, and nothing in the standard
report distinguishes them. The second woman will fracture, and the fracture
will be treated as a surprise even though four scans predicting it were already
sitting in her file. Bone loss is slow, measurable, and pharmacologically
treatable, which makes it close to the ideal target for trajectory monitoring,
and the reason nobody does it is that the prior scans are scattered across
years of PDFs from different hospitals on different manufacturers' machines,
and the T-scores on those PDFs are not directly comparable to each other.
Boensity is the layer that reconstructs the series, makes it comparable, and
draws the line.

## Architecture

Three services. The split follows the three genuinely different problems:
getting data out of documents, modelling it, and tracking whether anyone acted.

**Ingest** turns DEXA report PDFs into structured scans. It reads the embedded
text layer where one exists and falls back to OCR where it does not,
identifies the manufacturer, parses the results table with a vendor-specific
adapter, and converts every value onto a single standardised scale. It is the
service that makes a fifteen year history usable at all.

**Timeline** owns the longitudinal store and every number derived from it. It
fits the rate of decline, checks that rate against the scanner's own precision
error, projects the line forward with a prediction interval, detects where it
crosses the osteoporosis threshold, and computes the ten year fracture
probability using the WHO FRAX input variables. It answers the question the
DEXA report never has: when does this patient become someone I should have
treated already.

**Sync** tracks whether the treatment is being taken. The Timeline number is
conditional. A ten year risk that falls from 24 percent to 19 percent on
treatment is only true if the patient fills the prescription, and roughly half
of patients started on an oral bisphosphonate have stopped within a year. Sync
scores adherence against the fill record, flags supply gaps, and routes
referrals to pharmacies that can actually handle the drug in question.

## Engineering decisions

**TimescaleDB rather than plain PostgreSQL.** The per-patient dataset is
trivial. A patient with fifteen years of history has perhaps eight rows, and if
the only query were "give me one patient's scans" there would be no argument
for an extension. The query that decides the architecture is the department
view: who in this cohort is trending toward the threshold, which sweeps every
patient across a time window. That is a time-partitioned scan, and it is
exactly what chunk exclusion is built for. Three features earn their place:
hypertable partitioning on scan date with one year chunks, matched to the fact
that scan intervals are measured in years; continuous aggregates for cohort
rollups, refreshed on a policy rather than recomputed on every dashboard load;
and compression after five years, because nothing can ever be deleted. The
earliest scans are precisely the ones a long trajectory depends on. RDS
supports the extension, so this runs on managed Postgres rather than requiring
us to own patching and backups for a database holding patient data.

**Manufacturer-specific adapters rather than generic OCR.** A general document
extraction model can find the numbers on a DEXA report. It cannot know that
"Total" means the hip composite on a Hologic hip table and L1-L4 on a Hologic
spine table, that GE Lunar reports an "Upper Neck" region that is not the
femoral neck and must never be filed as one, or that Lunar's percentage columns
sit between its two score columns. Those are not OCR problems, they are domain
problems, and getting any of them wrong produces a plausible looking wrong
number, which is worse than a failure. The bigger reason is what happens after
extraction. Hologic and GE Lunar measure the same femoral neck and report
different BMD, and they compute T-scores against different young adult
reference databases. For a single scan that has never mattered. For a
trajectory it is fatal: a patient scanned on a Hologic in 2016 and a Lunar in
2022 shows a step change that is an artefact of the hardware. Boensity converts
every vendor BMD to standardised BMD using the published ICSBM cross
calibration regressions, then recomputes the T-score against one reference
database. That correction is the reason a cross-scanner trajectory is a real
measurement rather than a drawing. It is also the piece a generic extraction
approach cannot supply, because it requires knowing which machine produced the
number.

**D3 rather than a charting library.** What the trajectory chart draws is not a
line chart with a threshold annotation added on. The threshold band, the exact
point where the projection enters it, and the prediction interval widening as
the projection extends are the content, not decoration. A charting library
gives a line and a reference line and then has to be fought on every one of
those. D3 costs more code and gives exact control over the one screen the
entire product is judged on.

**FRAX as the credibility anchor.** Boensity could compute a proprietary risk
score. It would be worthless, because it would require a physician to accept a
new scale on trust. FRAX is what national intervention guidelines are already
written against. A physician reading 24 percent already knows what it means and
already knows the threshold at which they treat. The implementation is honest
about its status: Sheffield's regression coefficients are not published, so
this is an independent implementation of the same published methodology using a
Poisson competing risk model with relative risks from the Kanis meta-analyses.
It calibrates to within 1.1 percentage points of published FRAX values across
ages 50 to 85, with a regression test that fails the build if an incidence
table edit moves it. A regulated deployment routes the point estimate through a
licensed FRAX API and uses this implementation for the projection, which is the
part a licensed API cannot do.

## The chart

The FractureRiskChart shows a patient's measured scans as a solid line, the
modelled continuation as a dashed line, and the osteoporosis threshold as a red
band across the lower part of the plot. When the dashed line enters that band,
the chart header turns burnt coral and states the risk in words.

That state change is the product.

What a physician reads in it is not a number they were missing. It is a date
they were missing. The patient in front of them is at T -2.1, which is
osteopenia, which under current practice means monitor and rescan. The chart
says she reaches -2.5 in September 2027, that the rate driving that is 0.122
T-score units per year, that the rate is larger than the scanner's precision
error so it is real rather than noise, and that her ten year fracture
probability is already at the treatment threshold. The clinical decision
changes from "rescan in two years" to "treat now", and the entire argument for
that change is contained in one image.

Everything else in the interface exists to support that moment or to keep it
honest. The velocity panel shows the confidence interval on the slope. The
model quality label states how many scans the projection rests on. A change
smaller than the least significant change is reported as stable rather than as
a trend, because a warning system that manufactures urgency stops being
believed, and a warning nobody believes is worse than no warning. The chart is
allowed to be alarming only when the underlying measurement supports it.

## Revenue

Two layers. Physician software sold to endocrinology practices, orthopaedic
groups and hospital bone health units, and a pharmacy referral loop that routes
a patient with an identified adherence gap to a pharmacy equipped to handle
their drug. The clinical product is the wedge and the referral network is the
durable half. The detail of that model is not published here.

## Public and private split

The repository is public, with two components deliberately absent.

Public: the entire service scaffold, both manufacturer adapters, the
standardisation regressions, the full FRAX implementation, the trajectory and
threshold engines, the SMART on FHIR integration, the D3 chart, and all
infrastructure configuration.

Private: the composite adherence risk weighting in `adherence_scorer.py`, and
the pharmacy ranking and commission model in `referral_router.py`. Both live in
a package that is not committed. The public code imports them behind a guard
and reports `risk_score_available` or `routing_available` as false when they are
missing.

The split follows the moat rather than the effort. The clinical layer is the
harder engineering and it is public, because clinical software that a physician
cannot audit does not get adopted. A physician has to be able to read exactly
why a threshold crossing was flagged and exactly how a fracture probability was
computed. Hiding that would cost more in adoption than it could ever protect.
The proprietary pieces are the parts derived from pharmacy network outcome
data, which a competitor could not reconstruct from the code in any case
because the value is in the data behind the coefficients.

There is a second reason for the split, and it is clinical rather than
commercial. Referral routing has a clinical half and a commercial half:
deciding whether a patient needs a specialty pharmacy, and deciding which
pharmacy to send them to. The first is a clinical judgement and is public and
auditable. The second is a commercial judgement. Keeping them in separate
packages is the clearest possible demonstration that one is not quietly
influencing the other, and when the ranking package is absent the service
returns candidates in clinical eligibility order and explicitly reports that no
recommendation was made, rather than passing off list order as advice.

The same principle governs the degraded paths throughout. A missing composite
risk score returns as absent, never as zero, because zero reads as no risk.

## Build status

**Production ready.** The clinical calculation layer, which is the part that
has to be right. Trajectory fitting with confidence intervals and prediction
intervals. Least significant change handling, so a change inside the scanner
noise floor is never reported as a trend. FRAX with competing mortality,
calibrated to within 1.1 percentage points of published values and regression
tested. Threshold crossing detection with graded confidence. Cross-manufacturer
BMD standardisation. Both vendor adapters, tested end to end from real PDF
bytes. SMART on FHIR OAuth 2.0 with PKCE for both EHR and standalone launch,
tokens held server side. The chart, the risk badge, the adherence panel, and
the ingest review screen. 155 tests across the three services, with CI
enforcing lint, formatting, type checking, and a check that the demo fixtures
have not drifted from the engine that generates them.

**Demo ready.** Cloud Vision OCR is implemented on both the synchronous and
asynchronous paths and cleans up its intermediate objects, but has been
exercised against generated fixtures rather than a corpus of real scanned
faxes, so accuracy on degraded documents is unmeasured. The adapters read the
documented console layouts; real archives will contain variants, each of which
is a small addition rather than a rewrite. The TimescaleDB schema, hypertable,
continuous aggregates, and compression policies are written and valid but have
not been run against a cohort large enough to prove the query plans. Terraform
is written and CI validates it, but it has not been applied to a live AWS
account, so no plan has been reviewed against real quotas. The demo cohort is
five synthetic patients whose numbers are computed by the real engine rather
than written by hand, and it is still five patients.

**Scaffolded.** Patient record management: the platform currently receives a
patient context per request rather than owning a directory. Authentication
beyond the SMART launch: no local user store, no roles, no audit trail of who
viewed which chart. Both are conventional work with no unsolved problems in
them.

**Requires live pharmacy credentials.** Real fill history in place of the
fixture driver, referral submission reaching an actual pharmacy, acceptance and
decline callbacks, and dispensing confirmation to close the loop between a
referral and a fill. None of this is written and waiting to be switched on.
They are integration points that need a signed network agreement before they
can be built against, because each pharmacy network exposes a different
interface and several still operate by scheduled file drop.

The dependency runs one way. The physician-facing product is complete and
independent of the pharmacy layer. The pharmacy layer needs the physician
product in place to generate any signal at all, which is both the reason it is
the moat and the reason it is the part that is not yet built.
