"""Synthetic DEXA reports for testing.

Real reports cannot go in a repository, so these reproduce the layouts from
the vendor documentation: the header block, the column order, the region
labels, and the date formats each console emits. They are close enough to
exercise the adapters and deliberately do not pretend to be real patients.

Built with reportlab so they carry a genuine text layer, which means
pdf_parser runs its real code path rather than a mocked one.
"""

from __future__ import annotations

import io

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


def _render(lines: list[tuple[float, float, str]], font_size: int = 9) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setFont("Courier", font_size)
    for x, y, text in lines:
        pdf.drawString(x, y, text)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Hologic
# ---------------------------------------------------------------------------


def build_hologic_hip_report(
    scan_date: str = "03/14/2019",
    # BMD and T-score defaults are internally consistent with the NHANES III
    # reference Hologic scores the hip against, so a test comparing the printed
    # T-score to the recomputed one is checking the code rather than an
    # arbitrary number picked for the fixture.
    neck_bmd: float = 0.606,
    neck_t: float = -2.1,
    neck_z: float = -0.6,
    total_bmd: float = 0.735,
    total_t: float = -1.7,
    model: str = "Discovery A",
) -> bytes:
    y = 740.0
    rows = [
        (72, y, "Hologic QDR Bone Densitometry System"),
        (72, y - 14, f"System: {model}    Software Version 13.5.3:7"),
        (72, y - 28, "Meadowbrook Endocrinology, Bone Health Unit"),
        (72, y - 56, "Patient ID: MB-40912"),
        (72, y - 70, "Sex: Female    Ethnicity: White"),
        (72, y - 84, f"Scan Date: {scan_date}    Analyzed: {scan_date}"),
        (72, y - 112, "DualFemur Bone Density Results"),
        (72, y - 130, "Region   Area(cm2)  BMC(g)  BMD(g/cm2)  T-score  PR(%)  Z-score  AM(%)"),
        (
            72,
            y - 144,
            f"Neck        4.71     2.94      {neck_bmd:.3f}     {neck_t:.1f}    74     {neck_z:.1f}    92",
        ),
        (72, y - 158, "Troch       9.88     5.31       0.537     -1.5    80     -0.3    95"),
        (72, y - 172, "Inter      19.44    15.02       0.773     -1.3    84     -0.4    94"),
        (
            72,
            y - 186,
            f"Total      34.03    23.27      {total_bmd:.3f}     {total_t:.1f}    79     -0.5    93",
        ),
        (72, y - 200, "Ward's      1.06     0.42       0.396     -2.9    60     -1.1    83"),
        (72, y - 240, "WHO Classification: Osteopenia"),
    ]
    return _render(rows)


def build_hologic_spine_report(scan_date: str = "03/14/2019", total_t: float = -2.6) -> bytes:
    y = 740.0
    rows = [
        (72, y, "Hologic QDR Bone Densitometry System"),
        (72, y - 14, "System: Horizon A    APEX Software"),
        (72, y - 42, "Patient ID: MB-40912"),
        (72, y - 56, f"Scan Date: {scan_date}"),
        (72, y - 84, "AP Lumbar Spine Bone Density Results (L1-L4)"),
        (72, y - 102, "Region   Area(cm2)  BMC(g)  BMD(g/cm2)  T-score  PR(%)  Z-score  AM(%)"),
        (72, y - 116, "L1         11.52     8.11       0.704     -2.8    71     -1.2    84"),
        (72, y - 130, "L2         12.94     9.87       0.763     -2.5    74     -1.0    86"),
        (72, y - 144, "L3         13.71    10.94       0.798     -2.4    76     -0.9    88"),
        (72, y - 158, "L4         14.02    11.20       0.799     -2.4    76     -0.9    88"),
        (72, y - 172, f"Total      52.19    40.12       0.769     {total_t:.1f}    74     -1.0    86"),
    ]
    return _render(rows)


# ---------------------------------------------------------------------------
# GE Lunar
# ---------------------------------------------------------------------------


def build_lunar_hip_report(
    scan_date: str = "22.06.2022",
    neck_bmd: float = 0.712,
    neck_t: float = -1.6,
    neck_z: float = -0.4,
    total_bmd: float = 0.798,
    total_t: float = -1.3,
    model: str = "Prodigy Advance",
) -> bytes:
    y = 740.0
    rows = [
        (72, y, "GE Healthcare Lunar"),
        (72, y - 14, f"{model}    enCORE Software Version 16.2"),
        (72, y - 28, "Sankt Josef Klinik, Osteologie"),
        (72, y - 56, "Patient: LU-77315"),
        (72, y - 70, "Sex: Female"),
        (72, y - 84, f"Measured: {scan_date}    Analysed: {scan_date}"),
        (72, y - 112, "Left Femur Results"),
        (72, y - 130, "Region       BMD(g/cm2)  Young Adult(%)  T-score  Age Matched(%)  Z-score"),
        (
            72,
            y - 144,
            f"Neck              {neck_bmd:.3f}           81     {neck_t:.1f}             94     {neck_z:.1f}",
        ),
        (72, y - 158, "Upper Neck        0.641           78     -1.9             91     -0.6"),
        (72, y - 172, "Troch             0.649           85     -1.1             97     -0.2"),
        (72, y - 186, "Shaft             1.021           92     -0.7            102      0.1"),
        (
            72,
            y - 200,
            f"Total             {total_bmd:.3f}           84     {total_t:.1f}             96     -0.3",
        ),
        (72, y - 214, "Wards             0.502           64     -2.7             79     -1.0"),
    ]
    return _render(rows)


def build_lunar_spine_report(scan_date: str = "22.06.2022", total_t: float = -2.2) -> bytes:
    y = 740.0
    rows = [
        (72, y, "GE Healthcare Lunar iDXA"),
        (72, y - 14, "enCORE Software Version 17.0"),
        (72, y - 42, "Patient: LU-77315"),
        (72, y - 56, f"Measured: {scan_date}"),
        (72, y - 84, "AP Spine Results (L1-L4)"),
        (72, y - 102, "Region       BMD(g/cm2)  Young Adult(%)  T-score  Age Matched(%)  Z-score"),
        (72, y - 116, "L1                0.902           79     -2.4             90     -0.8"),
        (72, y - 130, "L2                0.941           81     -2.2             92     -0.7"),
        (72, y - 144, "L3                0.968           83     -2.0             94     -0.6"),
        (72, y - 158, "L4                0.955           82     -2.1             93     -0.6"),
        (72, y - 172, f"Total             0.943           81     {total_t:.1f}             92     -0.7"),
    ]
    return _render(rows)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def build_unbranded_report() -> bytes:
    """A report with the results table but no manufacturer marking.

    Happens with faxed copies where the letterhead did not survive, and with
    consolidated summaries a hospital generates from its own system.
    """
    y = 740.0
    rows = [
        (72, y, "Bone Densitometry Report"),
        (72, y - 28, "Patient ID: XX-0001"),
        (72, y - 42, "Scan Date: 2021-04-09"),
        (72, y - 70, "Region   Area(cm2)  BMC(g)  BMD(g/cm2)  T-score  Z-score"),
        (72, y - 84, "Neck        4.55     2.81       0.601     -2.3     -0.7"),
        (72, y - 98, "Total      33.10    22.04       0.666     -1.9     -0.5"),
    ]
    return _render(rows)


def build_image_only_pdf() -> bytes:
    """A PDF with effectively no text layer, standing in for a scanned fax."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setFont("Courier", 9)
    pdf.drawString(72, 740, ".")
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def build_no_date_report() -> bytes:
    y = 740.0
    rows = [
        (72, y, "Hologic QDR Bone Densitometry System"),
        (72, y - 14, "System: Discovery W"),
        (72, y - 42, "Patient ID: ND-5000"),
        (72, y - 70, "Region   Area(cm2)  BMC(g)  BMD(g/cm2)  T-score  PR(%)  Z-score  AM(%)"),
        (72, y - 84, "Neck        4.71     2.94       0.624     -2.1    74     -0.6    92"),
    ]
    return _render(rows)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def hologic_hip_pdf() -> bytes:
    return build_hologic_hip_report()


@pytest.fixture
def hologic_spine_pdf() -> bytes:
    return build_hologic_spine_report()


@pytest.fixture
def lunar_hip_pdf() -> bytes:
    return build_lunar_hip_report()


@pytest.fixture
def lunar_spine_pdf() -> bytes:
    return build_lunar_spine_report()


@pytest.fixture
def unbranded_pdf() -> bytes:
    return build_unbranded_report()


@pytest.fixture
def image_only_pdf() -> bytes:
    return build_image_only_pdf()


@pytest.fixture
def no_date_pdf() -> bytes:
    return build_no_date_report()
