"""Trajectory endpoints.

The response is deliberately fat. One call returns the points to draw, the
velocity, the crossings, and the alerts, because the timeline screen needs all
four to render a single coherent state and a physician should never watch
parts of a patient's risk picture arrive at different times.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status

from app.engine import frax_calculator, threshold_engine, trajectory_model
from app.schemas import (
    Envelope,
    Meta,
    ScanObservation,
    SkeletalSite,
    TrajectoryRequest,
    TrajectoryResponse,
)

router = APIRouter(prefix="/trajectory", tags=["trajectory"])


def _observation_span_years(scans: list[ScanObservation], site: SkeletalSite) -> float:
    at_site = [s.scan_date for s in scans if s.site == site]
    if len(at_site) < 2:
        return 0.0
    return (max(at_site) - min(at_site)).days / trajectory_model.DAYS_PER_YEAR


@router.post("", response_model=Envelope[TrajectoryResponse])
async def build_trajectory(request: TrajectoryRequest) -> Envelope[TrajectoryResponse]:
    """Fit the decline, project it forward, and report every threshold it meets."""
    try:
        fit = trajectory_model.fit_trajectory(request.scans, request.site, request.patient.sex)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    velocity = trajectory_model.build_velocity_report(fit, request.scans)
    points = trajectory_model.project_points(fit, request.scans, request.projection_years)
    crossings = threshold_engine.detect_all_crossings(fit, velocity, request.projection_years)

    at_site = sorted((s for s in request.scans if s.site == request.site), key=lambda s: s.scan_date)
    current_t_score = at_site[-1].t_score

    # FRAX only accepts a femoral neck measurement. Requesting a spine
    # trajectory is legitimate, but the absolute risk figure that feeds the
    # alerts has to come from the neck or not at all.
    femoral_neck = sorted(
        (s for s in request.scans if s.site == SkeletalSite.FEMORAL_NECK),
        key=lambda s: s.scan_date,
    )
    frax_result = frax_calculator.calculate_frax(
        request.patient,
        femoral_neck[-1].t_score if femoral_neck else None,
    )

    alerts = threshold_engine.build_alerts(
        fit=fit,
        velocity=velocity,
        crossings=crossings,
        scans=request.scans,
        current_t_score=current_t_score,
        frax=frax_result,
    )

    payload = TrajectoryResponse(
        patient_id=request.patient.patient_id,
        site=request.site,
        scan_count=len(at_site),
        observation_span_years=round(_observation_span_years(request.scans, request.site), 2),
        current_t_score=round(current_t_score, 2),
        current_band=trajectory_model.classify_band(current_t_score),
        points=points,
        velocity=velocity,
        crossings=crossings,
        alerts=alerts,
        model_quality=trajectory_model.describe_model_quality(fit, request.scans),
    )

    return Envelope(
        data=payload,
        error=None,
        meta=Meta(
            generated_at=datetime.now(UTC),
            least_significant_change=round(trajectory_model.least_significant_change(request.site), 3),
            projection_years=request.projection_years,
            baseline_date=fit.baseline_date.isoformat(),
        ),
    )


@router.post("/velocity", response_model=Envelope[dict])
async def velocity_only(request: TrajectoryRequest) -> Envelope[dict]:
    """Rate of change without the projection.

    Exists because the patient list screen renders a velocity column for
    hundreds of rows and does not need the point series for any of them.
    """
    try:
        fit = trajectory_model.fit_trajectory(request.scans, request.site, request.patient.sex)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    velocity = trajectory_model.build_velocity_report(fit, request.scans)
    return Envelope(
        data={
            "patient_id": request.patient.patient_id,
            "site": request.site.value,
            "velocity": velocity.model_dump(mode="json"),
            "model_quality": trajectory_model.describe_model_quality(fit, request.scans),
        },
        error=None,
        meta=Meta(generated_at=datetime.now(UTC)),
    )
