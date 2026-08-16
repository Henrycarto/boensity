# Pharmacy integration

## Why this service exists

The Timeline service can prove a patient needs treatment. It cannot tell you
whether they are getting it.

That gap is not marginal. Roughly half of patients started on an oral
bisphosphonate have stopped within a year. A FRAX probability that drops from
24 percent to 19 percent on treatment is a conditional claim, and the condition
is that the patient takes the drug.

Showing the projected benefit without showing adherence would overstate the
case on every screen in the product. That is why the adherence panel sits
directly beside the risk score on the patient timeline rather than on a
separate page.

## Connecting to a pharmacy network

Pharmacy networks do not share one API. Some expose NCPDP SCRIPT, some a REST
fill history, and several still work by SFTP drop of a delimited file.

`pharmacy_client.py` puts one interface in front of whatever a given partner
offers, so the scoring engine never learns which kind it is talking to.

```
PHARMACY_API_BASE_URL   endpoint issued per network
PHARMACY_API_KEY        bearer credential
PHARMACY_NETWORK_ID     sent as X-Network-Id
```

### Without credentials

The client runs a fixture driver against recorded response shapes. This is what
makes the rest of Sync developable and demonstrable before a network agreement
is signed.

It is honest about being fixtures. `live_pharmacy_data` appears in every
response `meta`, `/health` reports `live_pharmacy_connection`, and the
adherence panel renders a "Fixture data" label. A demo running on fixtures must
never be mistakable for a live pharmacy feed.

The fixture profiles are deterministic, keyed off the patient id. A demo where
the adherence panel changes on every reload is not a demo.

## Adherence scoring

### Proportion of days covered

PDC is the measure CMS, pharmacy benefit managers, and the adherence literature
all use. The 0.80 threshold is not administrative: below it, bisphosphonate
anti-fracture benefit drops sharply.

Implementing it openly matters. A pharmacy director will not act on a score
they cannot audit. "Our model says this patient is at risk" is not an argument.
"This patient covered 61 percent of days and has a 74 day gap" is.

Coverage is computed as a set of distinct covered days, not a sum of days
supply. A patient who refills early every month would otherwise score above 100
percent coverage while actually stockpiling.

### Medication possession ratio

Reported alongside PDC because they diverge in a clinically informative way. MPR
above 1.0 with PDC well below it means the patient is refilling but not taking,
which is a different conversation from not refilling.

### The injectable correction

This is the most damaging bug available in the module, so it gets its own
handling.

Pharmacy feeds record a six month denosumab injection with `days_supply` of 1,
because that is how their systems record an administration. Taking it literally
scores a perfectly adherent patient at 0.5 percent coverage.

`EXPECTED_COVERAGE_DAYS` maps each drug class to its real dosing interval, and
an injectable with an implausibly short days supply is corrected to it. A real
days supply on an injectable is respected, and oral days supply is never
overridden.

| Class | Expected cover |
|---|---|
| Oral bisphosphonate | 30 days |
| IV bisphosphonate | 365 days |
| Denosumab | 180 days |
| SERM | 30 days |
| Anabolic | 28 days |
| Supplement | 30 days |

### Gaps

A gap shorter than 30 days is a late refill, not a discontinuation. Flagging
every three day slip would bury the patients who genuinely stopped.

### Banding

| Band | Condition |
|---|---|
| Lapsed | No fill in 90 days, regardless of coverage |
| Adherent | PDC at or above 0.80 |
| Partial | PDC 0.50 to 0.80 |
| Non-adherent | PDC below 0.50 |

Recency wins over coverage. A patient can have high coverage across the year
and still have stopped four months ago, and reporting them as adherent would be
wrong in the direction that matters.

## Interventions

Escalation is by how far gone the patient is, not by how bad the number looks.

| Band | Action |
|---|---|
| Adherent | Nothing |
| Partial | SMS refill reminder, plus a pharmacist call if a gap exceeded 60 days |
| Non-adherent | Pharmacist call to review regimen and tolerance |
| Lapsed | Physician alert plus pharmacist call |

A lapsed patient does not get a text message. Past 90 days without a fill, a
reminder assumes an intention to continue that the record does not support.

## Referral routing

### Clinical eligibility, which is public

`referral_router.py` decides whether a patient needs a specialty pharmacy at
all, and filters candidates that can actually serve them.

Denosumab, IV bisphosphonates, and anabolic agents need cold chain handling,
scheduled administration, or prior authorisation support that a retail counter
cannot give. An oral bisphosphonate patient who is simply forgetting doses
needs a reminder, not a referral.

Below 40 percent coverage on an oral agent, no referral is raised. That level
usually reflects tolerance or belief, and moving the patient to another
pharmacy addresses neither. It routes to a prescriber review instead.

This logic is auditable and public because a physician has to be able to see
why their patient was routed.

### Commercial ranking, which is private

The ranking that orders qualified pharmacies against each other, the commission
model, and the network agreement terms live in `boensity_sync_private`, which
is not in this repository.

The split is not only commercial. A physician needs to know that clinical
eligibility is decided separately from commercial ranking, and keeping them in
different packages is the clearest way to demonstrate it.

When the private package is absent:

- `rank_candidates` returns candidates in clinical eligibility order.
- `match_score` is null and `match_score_available` is false.
- `routing_available` is false.
- No pharmacy is preselected.
- The referral card states that ordering is by clinical eligibility only.

Returning an unranked list as though it were ranked would pass off list order
as a recommendation, and would present a commercial judgement as a clinical
one.

The same pattern applies to `compute_risk_score` in `adherence_scorer.py`. The
composite weighting is private, and its absence returns None with
`risk_score_available` false rather than defaulting to zero, which would read
as no risk. PDC, MPR, gaps, persistence, and banding all still compute.

## What live credentials activate

Everything below needs a signed network agreement and issued credentials.
Nothing about it is written and waiting to be switched on; these are the
integration points, and each one needs a partner before it can be built
against.

- Real fill history in place of fixtures.
- Referral submission through `POST /referrals` reaching an actual pharmacy.
- Referral acceptance and decline callbacks.
- Dispensing confirmation, which closes the loop between a referral and a fill.

The physician-facing half of the product works without any of it. Ingest,
Timeline, the trajectory, the FRAX figures, and the chart are all independent
of the pharmacy layer.
