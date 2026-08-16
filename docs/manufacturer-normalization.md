# Manufacturer normalization

## The failure this prevents

A patient is scanned on a Hologic in 2016. The clinic replaces the machine. She
is scanned on a GE Lunar in 2022. Her bone density has not changed.

The two reports show different T-scores anyway.

Plot both points and fit a line through them and the trajectory reports a
change that never happened. The velocity is wrong, so the projected crossing
date is wrong, so the alert fires at the wrong time. Every downstream number in
Boensity rests on that line being real.

For a single scan none of this matters, which is why densitometry has lived
with it for thirty years. The physician reads the number the vendor printed and
treats accordingly. It only becomes a problem the moment you try to draw a
trend, and drawing the trend is the entire product.

## Two effects, stacked

**The densitometers disagree on BMD.** Different X-ray energies, different edge
detection, different bone maps. GE Lunar reads systematically higher at the
spine than Hologic on the same patient, by roughly ten percent.

**They compute T-scores against different young adult reference databases.** So
even after the BMD disagreement is corrected, the same bone density converts to
a different T-score depending on whose console printed it.

Correcting only the first would leave the second in place.

## The correction

Both are solved in one pass:

```
vendor BMD  ->  standardised BMD (sBMD)  ->  T-score vs one reference
```

### Step one: standardised BMD

The conversions come from the International Committee for Standards in Bone
Measurement cross-calibration studies, which scanned the same patients on
multiple manufacturers' machines and published the regressions.

sBMD is expressed in mg/cm2 by convention, which also makes it visually
impossible to confuse a standardised value with a raw vendor one.

| Site | Manufacturer | sBMD (mg/cm2) | Source |
|---|---|---|---|
| Lumbar spine | Hologic | 1000 × (1.0755 × BMD) | Genant 1994 |
| Lumbar spine | GE Lunar | 1000 × (0.9522 × BMD) | Genant 1994 |
| Total hip | Hologic | 1000 × (1.008 × BMD + 0.006) | Hanson 1997 |
| Total hip | GE Lunar | 1000 × (0.979 × BMD - 0.031) | Hanson 1997 |
| Femoral neck | Hologic | 1000 × (1.013 × BMD + 0.002) | Lu 2001 |
| Femoral neck | GE Lunar | 1000 × (0.939 × BMD + 0.032) | Lu 2001 |

### Step two: one reference database

The T-score is recomputed from sBMD against a single NHANES III derived young
adult reference, held in sBMD units so it sits on the same scale as every
converted measurement.

References are sex specific, following the ISCD position that men are scored
against a male normative database.

| Site | Sex | Young adult mean | SD |
|---|---|---|---|
| Femoral neck | female | 871.0 | 121.6 |
| Femoral neck | male | 944.1 | 133.7 |
| Total hip | female | 955.6 | 123.0 |
| Total hip | male | 1031.1 | 141.1 |
| Lumbar spine | female | 1126.0 | 118.3 |
| Lumbar spine | male | 1170.1 | 129.1 |

After this, a Hologic point and a Lunar point sit on the same axis and the
slope between them means something.

## What it does not fix

Cross-calibration regressions carry residual error of roughly 2 to 3 percent,
which is about 0.2 T-score units at the femoral neck. That is smaller than the
step it removes and it is not zero.

So a mixed-vendor series stays flagged all the way through. `schema_mapper`
records it, `trajectory_model.describe_model_quality` returns
`mixed_manufacturer_series_interpret_with_caution`, `threshold_engine` raises a
warning alert, and the chart footer shows it.

Before clinical deployment the coefficients should be verified against a
cross-calibration phantom on the actual scanners in use.

## When it cannot run

Standardisation needs three things: a BMD value, a known manufacturer, and the
patient's sex. Missing any one of them, the pipeline keeps the vendor T-score,
records why in the measurement notes, and forces the scan into human review.

| Missing | Behaviour |
|---|---|
| BMD on the report | Vendor T-score kept, noted as unstandardised |
| Manufacturer not identified | Vendor T-score kept, review forced |
| Patient sex not supplied | Vendor T-score kept, review forced |

Both T-scores are always retained. The vendor value is what is printed on the
report the patient may be holding, and the standardised value is the only one
valid to plot across scanners. The review screen shows them side by side, and a
gap larger than 0.5 raises a note, because that usually means the BMD or the
site was misread rather than that the standardisation is wrong.

## Reading the reports

### Manufacturer detection

Both adapters score the document against a weighted keyword fingerprint and the
stronger claim wins. Weighted rather than counted, because "Hologic" appearing
once is decisive while "Neck" appearing once means nothing.

Running both and comparing beats short-circuiting on the first keyword hit,
since service reports and multi-vendor comparison sheets mention a competitor's
name in passing. Below a detection score of 0.25 the manufacturer is recorded
as unknown rather than guessed, because mapping a report with the wrong adapter
produces plausible looking wrong numbers, which is the worst failure mode
available.

### Hologic

QDR, Discovery, Horizon, and APEX consoles. Fixed column order:

```
Region   Area(cm2)  BMC(g)  BMD(g/cm2)  T-score  PR(%)  Z-score  AM(%)
Neck        4.71     2.94      0.606      -2.1     74     -0.6     92
Troch       9.88     5.31      0.537      -1.5     80     -0.3     95
Inter      19.44    15.02      0.773      -1.3     84     -0.4     94
Total      34.03    23.27      0.735      -1.7     79     -0.5     93
```

Values are identified by the range they must fall in rather than by column
count: BMD is a small positive decimal, T and Z scores are signed and small,
percentages are large integers. That survives a missing BMC column, which older
QDR firmware omits.

The trap is the word **Total**. It means the hip composite on a hip table and
L1-L4 on a spine table. The table context decides, and getting it backwards
files a spine value as a hip value, which crosses two different diagnostic
thresholds.

### GE Lunar

Prodigy, iDXA, and DPX consoles running enCORE. Narrower columns, each score
paired with a percentage:

```
Region       BMD(g/cm2)  Young Adult(%)  T-score  Age Matched(%)  Z-score
Neck              0.712           81      -1.6            94       -0.4
Upper Neck        0.641           78      -1.9            91       -0.6
Total             0.798           84      -1.3            96       -0.3
```

Two traps here.

**The percentage columns sit between the two scores.** They are filtered by
magnitude: a percentage of young adult mean runs from roughly 50 to 130, well
clear of the signed score range.

**Lunar reports an Upper Neck region that Hologic does not.** It is not the
femoral neck and must not be filed as one. The site lookup takes the longest
matching label, so "upper neck" claims the string before "neck" can, and the
region is then dropped.

Lunar also has a date problem. European consoles print day first and US
consoles print month first, and a date where both parts are 12 or under is
genuinely ambiguous from the text alone. Boensity flags it rather than
guessing, because a swapped month silently reorders the patient's trajectory.

## Confidence

Every extracted value carries the confidence and the literal text it came from.
A number without provenance is not usable in a clinical record.

A PDF text layer starts at 0.97 because it is a lossless read of what the
console wrote. OCR starts at the engine's own reported confidence, capped at
0.94, because it is a reconstruction. Starting them equal would let a blurry
fax reach the timeline with the same authority as a native export.

A date found without a nearby label is knocked down to 0.6 and flagged, because
an unlabelled date could be the print date or the patient's birth date.

Below 0.80 overall the scan goes to human review.

## Adding a manufacturer

1. Add a fingerprint dict and a `detect()` function.
2. Add a site label mapping and an ignored region list.
3. Write `extract()` returning a `RawExtraction`.
4. Add the standardisation coefficients to `_STANDARDIZATION` in
   `schema_mapper.py`. Without them the vendor is detected but cannot be
   standardised, and every scan from it goes to review.
5. Register it in `detect_manufacturer`.
6. Add a synthetic report to `tests/conftest.py` reproducing the real column
   order and date format.

Norland coefficients are already present in the standardisation table even
though no Norland adapter exists, because a minority of European clinics still
run them and the day a Norland report arrives it should convert rather than
fail.
