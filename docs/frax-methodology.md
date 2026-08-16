# FRAX methodology

## What FRAX is and why Boensity anchors to it

FRAX is the clinical standard for absolute fracture risk. It takes eleven input
variables and returns two numbers: the ten year probability of a major
osteoporotic fracture (clinical spine, hip, forearm, or proximal humerus) and
the ten year probability of hip fracture specifically.

Intervention guidelines around the world are written against those two numbers.
The BHOF thresholds, 20 percent major osteoporotic and 3 percent hip, are what
a physician already uses to decide whether to treat.

That is the reason Boensity computes FRAX rather than inventing a score. A
proprietary risk number would require a clinician to accept a new scale on
trust. FRAX requires nothing new: they already know what 24 percent means, and
they already know what to do about it.

## What this implementation is not

The official FRAX regression coefficients and the country-specific calibration
tables held by the University of Sheffield are not published and are not
redistributable.

`frax_calculator.py` is an independent implementation of the same published
methodology. It reproduces FRAX behaviour and ordering closely, and it is not
the certified FRAX tool. Every response carries a `calibration_note` saying so.

Any regulated deployment routes the final point estimate through a licensed
FRAX API. This module then does the thing a licensed API cannot: recompute the
probability at future ages against the projected T-score, which is where the
trajectory becomes an argument rather than a chart.

## Method

### Competing risk

A patient who dies cannot go on to fracture. Ignoring competing mortality
overstates risk substantially in the over-eighties, which is the population
where the number matters most.

For each year of the window the model holds a fracture hazard and a death
hazard, and that year contributes:

```
P(fracture in year t) = S(t) * (h_f / (h_f + h_d)) * (1 - exp(-(h_f + h_d)))
S(t+1) = S(t) * exp(-(h_f + h_d))
```

Summing across ten years gives the probability. Each year is evaluated at its
midpoint, so a ten year window centres correctly rather than being biased a
full year young.

Incidence per 1000 person years is converted to a continuous hazard with
`-ln(1 - p)` rather than used directly. At the rates seen past 85 the two
differ enough to move a patient across an intervention threshold.

### Baseline epidemiology

Age and sex specific incidence tables for major osteoporotic fracture, hip
fracture, and all-cause mortality, linearly interpolated between five year
anchors.

These are European reference rates. FRAX is calibrated per country because
fracture incidence varies more than tenfold worldwide, so a deployment outside
Europe must replace them. That is the single most important thing to change
before clinical use, which is why the tables sit at the top of the file rather
than buried in a config.

### Clinical risk factors

Relative risks from the Kanis meta-analyses that the FRAX papers describe. Each
factor carries a separate RR for the major osteoporotic outcome and for hip,
because they diverge sharply. Smoking is a modest predictor of wrist fracture
and a strong predictor of hip fracture.

| Factor | RR major | RR hip |
|---|---|---|
| Previous fragility fracture | 1.86 | 1.62 |
| Parent hip fracture | 1.54 | 2.27 |
| Current smoker | 1.29 | 1.84 |
| Glucocorticoid exposure | 1.66 | 2.13 |
| Rheumatoid arthritis | 1.95 | 1.95 |
| Secondary osteoporosis | 1.50 | 1.60 |
| Alcohol 3 or more units daily | 1.38 | 1.68 |

### BMD mediation

Part of each clinical factor's effect runs through bone density. When a femoral
neck T-score is supplied, that portion is already counted by the BMD term, and
applying it twice would double count.

Each factor carries a `bmd_mediated_fraction`, and the effective relative risk
becomes `RR ** (1 - fraction)` when BMD is present.

Secondary osteoporosis has a fraction of 1.0, so it reduces to exactly 1.0 once
a measurement exists. That matches FRAX, which drops it entirely in that case.

### The BMD term, and the mistake it is easy to make

Relative risk from bone density is measured in standard deviations below the
**population mean for that age and sex**, not below the young adult mean.

This is the single easiest way to build a FRAX clone that reads high on every
patient. The baseline incidence rates are population averages, which already
contain the average bone density of that age group. An average 65 year old
woman sits near T -1.3, not at T 0. Scoring her BMD relative risk against the
young adult mean would charge her for bone loss that the baseline rate already
counts.

`_MEAN_FEMORAL_NECK_T_SCORE` holds the age and sex specific means, and a
patient exactly at the average for their age gets a multiplier of exactly 1.0.
There is a test that asserts precisely this.

The gradient of risk per standard deviation is age dependent for hip, falling
from 3.68 at age 50 to 1.71 at 85. A low T-score at 50 is far more
discriminating than the same T-score at 85, where competing frailty dominates.
Ignoring that decay is the second most common error in reimplementations.

T-scores are clamped to the range -4.0 to +2.0. Beyond those bounds the source
cohorts hold almost no data and the exponential would run away into risks
nobody has ever observed.

### BMI

Risk rises steeply below BMI 25 and falls modestly above it. Once a real BMD
measurement is present most of the BMI signal is redundant, so it is damped to
the power of 0.25 rather than dropped outright.

### Hip cannot exceed the composite

Hip fracture is a member of the major osteoporotic composite, so a hip
probability above the composite is arithmetically impossible. Extreme inputs
can push the two relative risk chains far enough apart to produce it, so the
result is clamped and there is a test that sweeps ages and T-scores checking
the identity holds.

## Calibration

The tables were fitted to reproduce published FRAX probabilities for a woman
with no clinical risk factors, BMI near 24, and no BMD entered.

| Age | Boensity major | Published | Boensity hip | Published |
|---|---|---|---|---|
| 50 | 6.5% | 7.0% | 0.3% | 0.6% |
| 55 | 7.8% | 8.0% | 0.5% | 0.8% |
| 60 | 9.2% | 9.5% | 0.9% | 1.0% |
| 65 | 11.2% | 11.0% | 1.6% | 1.4% |
| 70 | 13.7% | 13.0% | 2.7% | 2.3% |
| 75 | 16.1% | 15.0% | 4.0% | 3.6% |
| 80 | 16.7% | 16.0% | 5.5% | 5.0% |
| 85 | 15.1% | 16.0% | 6.2% | 6.0% |

Maximum deviation is 1.1 percentage points. `TestCalibration` asserts this
holds within 2 points at every age, so an incidence edit that quietly moves
every patient in the system fails the build.

The plateau and decline in the major osteoporotic figure past 80 is the
competing mortality term, not a bug. Fracture incidence keeps climbing there,
but so does death, and the ten year probability cannot climb without limit.

## Treatment effect

Anti-fracture efficacy from the pivotal randomised trials, applied as a hazard
ratio.

| Agent | HR major | HR hip |
|---|---|---|
| Oral bisphosphonate (alendronate class) | 0.80 | 0.60 |
| Denosumab | 0.75 | 0.60 |

The treated probability is returned alongside the untreated one because that
pair is the whole physician argument. Percent reductions are abstract. Two
absolute probabilities side by side are a decision.

Number needed to treat is derived from the absolute risk reduction and returned
with them.

## Projected FRAX

`POST /frax/projected` recomputes the probability at future ages against the
projected femoral neck T-score.

Both inputs move together: the patient ages and their bone density falls.
Holding either fixed understates the curve, and the entire argument for early
intervention lives in how steeply this rises.

The projection stops at age 90, because FRAX is validated from 40 to 90 and
past that the model would extrapolate into ages where the source cohorts hold
almost no data.

## Limitations to state plainly

- Not the certified Sheffield tool, and not calibrated to a specific national
  cohort.
- European reference incidence. Replace the tables for any other region.
- Validated input range is age 40 to 90.
- FRAX uses femoral neck BMD by definition. A spine trajectory is clinically
  useful and cannot feed the BMD-adjusted figure.
- The model takes no account of falls risk, which is a major independent
  contributor to hip fracture that FRAX itself also omits.
- Trabecular bone score, recency of fracture, and glucocorticoid dose
  adjustments exist in newer FRAX guidance and are not implemented here.
