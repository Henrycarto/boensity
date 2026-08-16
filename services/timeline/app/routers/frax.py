"""FRAX endpoints.

/frax answers "what is this patient's risk today".
/frax/projected answers "what does it become if nothing changes", which is the
question static DEXA reporting has never been able to answer.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.engine import frax_calculator, trajectory_model
from app.schemas import (
    Envelope,
    FraxRequest,
    FraxResponse,
    Meta,
    PatientContext,
    ProjectedFraxPoint,
    ScanObservation,
    SkeletalSite,
    TreatmentDelta,
)

router = APIRouter(prefix="/frax", tags=["frax"])


@router.post("", response_model=Envelope[FraxResponse])
async def calculate(request: FraxRequest) -> Envelope[FraxResponse]:
    result = frax_calculator.calculate_frax(request.patient, request.femoral_neck_t_score)
    threshold_met, basis = frax_calculator.meets_intervention_threshold(result)

    treatment: TreatmentDelta | None = None
    if request.include_treatment_delta and request.treatment != "none":
        treatment = TreatmentDelta(**frax_calculator.apply_treatment(result, request.treatment))

    payload = FraxResponse(
        patient_id=request.patient.patient_id,
        major_osteoporotic_percent=result.major_osteoporotic_percent,
        hip_percent=result.hip_percent,
        bmd_included=result.bmd_included,
        intervention_threshold_met=threshold_met,
        threshold_basis=basis,
        contributing_factors=result.contributing_factors,
        treatment=treatment,
        calibration_note=frax_calculator.CALIBRATION_NOTE,
    )

    return Envelope(
        data=payload,
        error=None,
        meta=Meta(
            generated_at=datetime.now(UTC),
            relative_risk_major=result.relative_risk_major,
            relative_risk_hip=result.relative_risk_hip,
            threshold_major=frax_calculator.INTERVENTION_THRESHOLD_MAJOR,
            threshold_hip=frax_calculator.INTERVENTION_THRESHOLD_HIP,
        ),
    )


class ProjectedFraxRequest(BaseModel):
    patient: PatientContext
    scans: list[ScanObservation] = Field(min_length=1)
    projection_years: int = Field(default=10, ge=1, le=20)
    step_years: int = Field(default=2, ge=1, le=5)


@router.post("/projected", response_model=Envelope[list[ProjectedFraxPoint]])
async def projected(request: ProjectedFraxRequest) -> Envelope[list[ProjectedFraxPoint]]:
    """FRAX recomputed at future ages against the projected femoral neck T-score.

    Both inputs move together: the patient ages and their bone density falls.
    Holding either one fixed understates the curve, and the whole argument for
    early intervention lives in how steeply this rises.
    """
    femoral_neck = [s for s in request.scans if s.site == SkeletalSite.FEMORAL_NECK]
    if not femoral_neck:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="projected FRAX requires at least one femoral neck scan",
        )

    try:
        fit = trajectory_model.fit_trajectory(request.scans, SkeletalSite.FEMORAL_NECK, request.patient.sex)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    last_observed_years = float(fit.x_values[-1])
    points: list[ProjectedFraxPoint] = []

    for offset in range(0, request.projection_years + 1, request.step_years):
        projected_t = fit.value_at(last_observed_years + offset)
        future_age = request.patient.age + offset

        # FRAX is only validated to 90. Past that the model would extrapolate
        # into ages where the source cohorts hold almost no data.
        if future_age > 90.0:
            break

        aged_patient = request.patient.model_copy(update={"age": future_age})
        result = frax_calculator.calculate_frax(aged_patient, projected_t)
        points.append(
            ProjectedFraxPoint(
                year_offset=offset,
                age=round(future_age, 1),
                projected_t_score=round(projected_t, 2),
                major_osteoporotic_percent=result.major_osteoporotic_percent,
                hip_percent=result.hip_percent,
            )
        )

    return Envelope(
        data=points,
        error=None,
        meta=Meta(
            generated_at=datetime.now(UTC),
            calibration_note=frax_calculator.CALIBRATION_NOTE,
            t_score_per_year=round(fit.slope_per_year, 4),
        ),
    )
