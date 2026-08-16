"""Adapter and normalisation tests, run end to end from real PDF bytes."""

from datetime import date

import pytest

from app.engine import ge_lunar_adapter, hologic_adapter, pdf_parser, schema_mapper
from app.schemas import ExtractionSource, Manufacturer, SkeletalSite


def parse(pdf_bytes: bytes):
    return pdf_parser.parse_pdf(pdf_bytes)


class TestPdfParser:
    def test_reads_a_text_layer(self, hologic_hip_pdf):
        document = parse(hologic_hip_pdf)
        assert document.has_usable_text_layer
        assert document.page_count == 1
        assert "Hologic" in document.full_text

    def test_groups_words_into_lines(self, hologic_hip_pdf):
        document = parse(hologic_hip_pdf)
        neck_lines = document.find_lines(r"^Neck\b")
        assert len(neck_lines) == 1
        assert "0.606" in neck_lines[0].text

    def test_flags_a_document_with_no_usable_text(self, image_only_pdf):
        document = parse(image_only_pdf)
        assert not document.has_usable_text_layer

    def test_ocr_text_wraps_into_the_same_structure(self):
        document = pdf_parser.parsed_from_ocr_text("Neck 0.624 -2.1\nTotal 0.684 -1.7")
        assert len(document.lines) == 2
        assert document.lines[0].text.startswith("Neck")


class TestManufacturerDetection:
    def test_identifies_hologic(self, hologic_hip_pdf):
        manufacturer, score = schema_mapper.detect_manufacturer(parse(hologic_hip_pdf))
        assert manufacturer is Manufacturer.HOLOGIC
        assert score >= schema_mapper.MIN_DETECTION_SCORE

    def test_identifies_ge_lunar(self, lunar_hip_pdf):
        manufacturer, score = schema_mapper.detect_manufacturer(parse(lunar_hip_pdf))
        assert manufacturer is Manufacturer.GE_LUNAR
        assert score >= schema_mapper.MIN_DETECTION_SCORE

    def test_unbranded_report_is_not_guessed(self, unbranded_pdf):
        manufacturer, _ = schema_mapper.detect_manufacturer(parse(unbranded_pdf))
        assert manufacturer is Manufacturer.UNKNOWN

    def test_hologic_scores_higher_than_lunar_on_a_hologic_report(self, hologic_hip_pdf):
        document = parse(hologic_hip_pdf)
        assert hologic_adapter.detect(document) > ge_lunar_adapter.detect(document)

    def test_lunar_scores_higher_than_hologic_on_a_lunar_report(self, lunar_hip_pdf):
        document = parse(lunar_hip_pdf)
        assert ge_lunar_adapter.detect(document) > hologic_adapter.detect(document)


class TestHologicAdapter:
    def test_reads_the_hip_table(self, hologic_hip_pdf):
        extraction = hologic_adapter.extract(parse(hologic_hip_pdf))
        assert extraction.manufacturer is Manufacturer.HOLOGIC

        by_site = {m.site: m for m in extraction.measurements}
        assert SkeletalSite.FEMORAL_NECK in by_site
        assert SkeletalSite.TOTAL_HIP in by_site

        neck = by_site[SkeletalSite.FEMORAL_NECK]
        assert neck.bmd_g_cm2.value == pytest.approx(0.606)
        assert neck.t_score.value == pytest.approx(-2.1)
        assert neck.z_score.value == pytest.approx(-0.6)

    def test_reads_the_scan_date(self, hologic_hip_pdf):
        extraction = hologic_adapter.extract(parse(hologic_hip_pdf))
        assert extraction.scan_date.value == date(2019, 3, 14)
        assert extraction.scan_date.confidence > 0.9

    def test_reads_the_scanner_model(self, hologic_hip_pdf):
        extraction = hologic_adapter.extract(parse(hologic_hip_pdf))
        assert "Discovery" in str(extraction.scanner_model.value)

    def test_ignores_wards_and_trochanter(self, hologic_hip_pdf):
        extraction = hologic_adapter.extract(parse(hologic_hip_pdf))
        labels = " ".join(m.region_label.lower() for m in extraction.measurements)
        assert "ward" not in labels
        assert "troch" not in labels

    def test_spine_total_maps_to_lumbar_not_hip(self, hologic_spine_pdf):
        """ "Total" is the hip composite on a hip table and L1-L4 on a spine
        table. Filing one as the other crosses two different thresholds."""
        extraction = hologic_adapter.extract(parse(hologic_spine_pdf))
        sites = {m.site for m in extraction.measurements}
        assert SkeletalSite.LUMBAR_SPINE in sites
        assert SkeletalSite.TOTAL_HIP not in sites

        spine = next(m for m in extraction.measurements if m.site is SkeletalSite.LUMBAR_SPINE)
        assert spine.t_score.value == pytest.approx(-2.6)

    def test_missing_date_is_flagged_not_defaulted_silently(self, no_date_pdf):
        extraction = hologic_adapter.extract(parse(no_date_pdf))
        assert extraction.scan_date is None or extraction.scan_date.confidence < 0.8

    def test_negative_scores_keep_their_sign(self, hologic_hip_pdf):
        extraction = hologic_adapter.extract(parse(hologic_hip_pdf))
        neck = next(m for m in extraction.measurements if m.site is SkeletalSite.FEMORAL_NECK)
        assert neck.t_score.value < 0


class TestGeLunarAdapter:
    def test_reads_the_femur_table(self, lunar_hip_pdf):
        extraction = ge_lunar_adapter.extract(parse(lunar_hip_pdf))
        assert extraction.manufacturer is Manufacturer.GE_LUNAR

        by_site = {m.site: m for m in extraction.measurements}
        neck = by_site[SkeletalSite.FEMORAL_NECK]
        assert neck.bmd_g_cm2.value == pytest.approx(0.712)
        assert neck.t_score.value == pytest.approx(-1.6)

    def test_upper_neck_is_not_treated_as_femoral_neck(self, lunar_hip_pdf):
        """Lunar reports a separate Upper Neck region. Its T-score of -1.9 must
        not end up in the femoral neck slot, whose value is -1.6."""
        extraction = ge_lunar_adapter.extract(parse(lunar_hip_pdf))
        neck = next(m for m in extraction.measurements if m.site is SkeletalSite.FEMORAL_NECK)
        assert neck.t_score.value == pytest.approx(-1.6)
        assert "upper" not in neck.region_label.lower()

    def test_reads_a_european_dotted_date(self, lunar_hip_pdf):
        extraction = ge_lunar_adapter.extract(parse(lunar_hip_pdf))
        assert extraction.scan_date.value == date(2022, 6, 22)

    def test_percentage_columns_are_not_mistaken_for_scores(self, lunar_hip_pdf):
        extraction = ge_lunar_adapter.extract(parse(lunar_hip_pdf))
        for measurement in extraction.measurements:
            if measurement.t_score is not None:
                assert -8.0 <= measurement.t_score.value <= 6.0

    def test_ambiguous_date_order_is_flagged(self):
        from tests.conftest import build_lunar_hip_report

        # 06.07.2021 could be 6 July or 7 June depending on console locale.
        extraction = ge_lunar_adapter.extract(parse(build_lunar_hip_report(scan_date="06.07.2021")))
        assert any("ambiguous" in warning for warning in extraction.warnings)

    def test_unambiguous_date_is_not_flagged(self, lunar_hip_pdf):
        extraction = ge_lunar_adapter.extract(parse(lunar_hip_pdf))
        assert not any("ambiguous" in warning for warning in extraction.warnings)

    def test_spine_report(self, lunar_spine_pdf):
        extraction = ge_lunar_adapter.extract(parse(lunar_spine_pdf))
        sites = {m.site for m in extraction.measurements}
        assert SkeletalSite.LUMBAR_SPINE in sites


class TestStandardization:
    def test_young_adult_mean_maps_to_t_score_zero(self):
        """A Hologic femoral neck BMD at the NHANES III young adult female mean
        must standardise to a T-score of 0. If this drifts, every recomputed
        T-score in the system is offset."""
        sbmd, _ = schema_mapper.standardize_bmd(0.858, SkeletalSite.FEMORAL_NECK, Manufacturer.HOLOGIC)
        t_score = schema_mapper.t_score_from_sbmd(sbmd, SkeletalSite.FEMORAL_NECK, "female")
        assert t_score == pytest.approx(0.0, abs=0.05)

    def test_the_two_vendors_converge_after_standardisation(self):
        """The whole point of the module.

        These two BMD values are what the ICSBM regressions say the same
        femoral neck reads as on each machine. Before standardisation they are
        different numbers; afterwards they must land on the same T-score,
        because otherwise a trajectory spanning a scanner change is fiction.
        """
        hologic_sbmd, _ = schema_mapper.standardize_bmd(
            0.700, SkeletalSite.FEMORAL_NECK, Manufacturer.HOLOGIC
        )
        # Solve the Lunar regression for the BMD giving the same sBMD.
        lunar_bmd = ((hologic_sbmd / 1000.0) - 0.032) / 0.939
        lunar_sbmd, _ = schema_mapper.standardize_bmd(
            lunar_bmd, SkeletalSite.FEMORAL_NECK, Manufacturer.GE_LUNAR
        )

        assert lunar_sbmd == pytest.approx(hologic_sbmd, abs=1.0)

        t_hologic = schema_mapper.t_score_from_sbmd(hologic_sbmd, SkeletalSite.FEMORAL_NECK, "female")
        t_lunar = schema_mapper.t_score_from_sbmd(lunar_sbmd, SkeletalSite.FEMORAL_NECK, "female")
        assert t_hologic == pytest.approx(t_lunar, abs=0.02)

    def test_lower_bmd_gives_a_lower_t_score(self):
        high, _ = schema_mapper.standardize_bmd(0.800, SkeletalSite.FEMORAL_NECK, Manufacturer.HOLOGIC)
        low, _ = schema_mapper.standardize_bmd(0.600, SkeletalSite.FEMORAL_NECK, Manufacturer.HOLOGIC)
        assert schema_mapper.t_score_from_sbmd(
            low, SkeletalSite.FEMORAL_NECK, "female"
        ) < schema_mapper.t_score_from_sbmd(high, SkeletalSite.FEMORAL_NECK, "female")

    def test_men_are_scored_against_a_male_reference(self):
        sbmd, _ = schema_mapper.standardize_bmd(0.700, SkeletalSite.FEMORAL_NECK, Manufacturer.HOLOGIC)
        female_t = schema_mapper.t_score_from_sbmd(sbmd, SkeletalSite.FEMORAL_NECK, "female")
        male_t = schema_mapper.t_score_from_sbmd(sbmd, SkeletalSite.FEMORAL_NECK, "male")
        assert male_t != female_t
        # A male young adult mean is higher, so the same bone scores lower.
        assert male_t < female_t

    def test_unknown_manufacturer_cannot_be_standardised(self):
        sbmd, citation = schema_mapper.standardize_bmd(0.700, SkeletalSite.FEMORAL_NECK, Manufacturer.UNKNOWN)
        assert sbmd is None
        assert citation is None


class TestNormalization:
    def test_hologic_scan_normalises_with_a_recomputed_t_score(self, hologic_hip_pdf):
        extraction = hologic_adapter.extract(parse(hologic_hip_pdf))
        scan = schema_mapper.normalize(extraction, patient_id="pt-1", sex="female")

        neck = next(m for m in scan.measurements if m.site is SkeletalSite.FEMORAL_NECK)
        assert neck.t_score_is_recomputed
        assert neck.standardized_bmd_mg_cm2 is not None
        assert neck.vendor_t_score == pytest.approx(-2.1)
        # The fixture BMD is consistent with the NHANES III reference Hologic
        # uses, so the recomputed value should land essentially on the printed
        # one. A drift here means the standardisation or the reference moved.
        assert neck.t_score == pytest.approx(-2.1, abs=0.1)

    def test_scanner_model_does_not_span_a_line_break(self, lunar_hip_pdf):
        extraction = ge_lunar_adapter.extract(parse(lunar_hip_pdf))
        model = str(extraction.scanner_model.value)
        assert "\n" not in model
        assert model.strip() == model

    def test_without_sex_the_vendor_t_score_is_kept(self, hologic_hip_pdf):
        extraction = hologic_adapter.extract(parse(hologic_hip_pdf))
        scan = schema_mapper.normalize(extraction, patient_id="pt-1", sex=None)

        neck = next(m for m in scan.measurements if m.site is SkeletalSite.FEMORAL_NECK)
        assert not neck.t_score_is_recomputed
        assert neck.t_score == pytest.approx(-2.1)
        assert scan.requires_review
        assert any("sex" in reason for reason in scan.review_reasons)

    def test_lunar_scan_normalises(self, lunar_hip_pdf):
        extraction = ge_lunar_adapter.extract(parse(lunar_hip_pdf))
        scan = schema_mapper.normalize(extraction, patient_id="pt-2", sex="female")

        assert scan.manufacturer is Manufacturer.GE_LUNAR
        assert scan.scan_date == date(2022, 6, 22)
        neck = next(m for m in scan.measurements if m.site is SkeletalSite.FEMORAL_NECK)
        assert neck.t_score_is_recomputed

    def test_unknown_manufacturer_forces_review(self, unbranded_pdf):
        document = parse(unbranded_pdf)
        extraction = schema_mapper.extract_with_best_adapter(document)
        scan = schema_mapper.normalize(extraction, patient_id="pt-3", sex="female")

        assert scan.manufacturer is Manufacturer.UNKNOWN
        assert scan.requires_review
        assert any("manufacturer" in reason for reason in scan.review_reasons)
        # It still recovers the printed values so a human has something to confirm.
        assert scan.measurements

    def test_missing_date_forces_review(self, no_date_pdf):
        extraction = hologic_adapter.extract(parse(no_date_pdf))
        scan = schema_mapper.normalize(extraction, patient_id="pt-4", sex="female")
        assert scan.requires_review
        assert any("date" in reason for reason in scan.review_reasons)

    def test_confident_scan_does_not_require_review(self, hologic_hip_pdf):
        extraction = hologic_adapter.extract(parse(hologic_hip_pdf))
        scan = schema_mapper.normalize(extraction, patient_id="pt-5", sex="female")
        assert not scan.requires_review
        assert scan.overall_confidence >= schema_mapper.REVIEW_CONFIDENCE_FLOOR

    def test_notes_explain_every_transformation(self, hologic_hip_pdf):
        extraction = hologic_adapter.extract(parse(hologic_hip_pdf))
        scan = schema_mapper.normalize(extraction, patient_id="pt-6", sex="female")
        neck = next(m for m in scan.measurements if m.site is SkeletalSite.FEMORAL_NECK)
        assert any("recomputed" in note for note in neck.notes)


class TestCrossVendorTrajectory:
    def test_a_scanner_change_does_not_create_a_phantom_step(self):
        """The failure this whole module exists to prevent.

        A patient is scanned on Hologic, then the clinic replaces the machine
        with a Lunar. Their bone density is genuinely unchanged. The raw vendor
        T-scores differ anyway. After standardisation the two points must agree,
        or the fitted trajectory reports a decline that never happened.
        """
        from tests.conftest import build_hologic_hip_report, build_lunar_hip_report

        hologic_bmd = 0.700
        # The Lunar reading of the same bone, per the ICSBM regressions.
        sbmd = 1000.0 * (1.013 * hologic_bmd + 0.002)
        lunar_bmd = ((sbmd / 1000.0) - 0.032) / 0.939

        hologic_scan = schema_mapper.normalize(
            hologic_adapter.extract(parse(build_hologic_hip_report(neck_bmd=hologic_bmd, neck_t=-1.4))),
            sex="female",
        )
        lunar_scan = schema_mapper.normalize(
            ge_lunar_adapter.extract(
                parse(build_lunar_hip_report(neck_bmd=round(lunar_bmd, 3), neck_t=-1.1))
            ),
            sex="female",
        )

        h_neck = next(m for m in hologic_scan.measurements if m.site is SkeletalSite.FEMORAL_NECK)
        l_neck = next(m for m in lunar_scan.measurements if m.site is SkeletalSite.FEMORAL_NECK)

        # The printed vendor T-scores disagree by 0.3, which would read as
        # bone gain between the two visits.
        assert abs(h_neck.vendor_t_score - l_neck.vendor_t_score) == pytest.approx(0.3, abs=0.01)

        # After standardisation the artefact is gone.
        assert h_neck.t_score == pytest.approx(l_neck.t_score, abs=0.05)


class TestExtractionSourceConfidence:
    def test_ocr_is_trusted_less_than_a_text_layer(self, hologic_hip_pdf):
        document = parse(hologic_hip_pdf)
        from_text = hologic_adapter.extract(document, ExtractionSource.PDF_TEXT_LAYER)
        from_ocr = hologic_adapter.extract(document, ExtractionSource.CLOUD_VISION_OCR, 0.82)

        text_confidence = from_text.measurements[0].bmd_g_cm2.confidence
        ocr_confidence = from_ocr.measurements[0].bmd_g_cm2.confidence
        assert ocr_confidence < text_confidence
