import json
import os
import shutil
from io import BytesIO

import pandas as pd
import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

os.environ.setdefault("OMR_ALLOW_NO_AUTH", "1")

from api import main as api_main
from src.entry import process_single_file
from src.tests.test_samples.sample1.boilerplate import TEMPLATE_BOILERPLATE
from src.tests.utils import setup_mocker_patches


def test_check_endpoint_declares_template_id_as_multipart_form_field():
    check_route = next(
        route
        for route in api_main.app.routes
        if getattr(route, "path", None) == "/check"
        and "POST" in getattr(route, "methods", set())
    )

    body_param_names = {param.name for param in check_route.dependant.body_params}
    query_param_names = {param.name for param in check_route.dependant.query_params}

    assert {
        "image",
        "template_id",
        "evaluate",
        "evaluation",
        "school_id",
        "exam_id",
        "require_roll",
        "pre_rectified",
        "marker_center_rectified",
    } <= body_param_names
    assert "template_id" not in query_param_names
    assert "evaluate" not in query_param_names


def test_pre_rectified_request_disables_alignment_and_perspective_warp():
    template = {
        "preProcessors": [
            {
                "name": "FeatureBasedAlignment",
                "options": {"reference": "reference.png"},
            },
            {
                "name": "CropOnMarkers",
                "options": {"relativePath": "omr_marker.jpg"},
            },
        ],
    }

    configured = api_main._configure_template_for_request(
        template,
        pre_rectified=True,
    )

    assert [item["name"] for item in configured["preProcessors"]] == [
        "CropOnMarkers",
    ]
    assert configured["preProcessors"][0]["options"]["crop_mode"] == "axis_aligned"
    assert "crop_mode" not in template["preProcessors"][1]["options"]


def test_marker_center_rectified_request_skips_all_geometric_preprocessors():
    template = {
        "preProcessors": [
            {
                "name": "FeatureBasedAlignment",
                "options": {"reference": "reference.png"},
            },
            {
                "name": "CropOnMarkers",
                "options": {"relativePath": "omr_marker.jpg"},
            },
            {
                "name": "GaussianBlur",
                "options": {"kernelSize": [3, 3]},
            },
        ],
    }

    configured = api_main._configure_template_for_request(
        template,
        pre_rectified=True,
        marker_center_rectified=True,
    )

    assert [item["name"] for item in configured["preProcessors"]] == [
        "GaussianBlur",
    ]


def test_non_rectified_request_preserves_template_preprocessors():
    template = {
        "preProcessors": [
            {
                "name": "FeatureBasedAlignment",
                "options": {"reference": "reference.png"},
            },
            {
                "name": "CropOnMarkers",
                "options": {"relativePath": "omr_marker.jpg"},
            },
        ],
    }

    configured = api_main._configure_template_for_request(
        template,
        pre_rectified=False,
    )

    assert configured == template


def test_failed_debug_image_retention_is_opt_in_and_bounded(
    monkeypatch,
    tmp_path,
):
    source = tmp_path / "upload.jpg"
    source.write_bytes(b"private-test-image")
    debug_dir = tmp_path / "failed"
    monkeypatch.setattr(api_main, "DEBUG_SAVE_FAILED_IMAGES", True)
    monkeypatch.setattr(api_main, "DEBUG_FAILED_IMAGE_MAX", 2)
    monkeypatch.setattr(api_main, "DEBUG_FAILED_IMAGE_DIR", debug_dir)

    for request_id in ("request-1", "request-2", "request-3"):
        retained = api_main._persist_failed_debug_image(
            source,
            request_id,
            ".jpg",
        )
        assert retained is not None

    retained_files = list(debug_dir.glob("*.jpg"))
    assert len(retained_files) == 2
    assert (debug_dir / "request-3.jpg").is_file()


def test_check_endpoint_uses_in_memory_result(monkeypatch):
    monkeypatch.setattr(
        api_main,
        "entry_point",
        lambda _work_dir, _args: {
            "file_id": "upload.jpg",
            "input_path": "upload.jpg",
            "output_path": "upload.jpg",
            "score": 3.0,
            "responses": {
                "Roll": "01234",
                "q1": "A",
                "q2": "",
            },
        },
    )
    upload = UploadFile(
        filename="sheet.jpg",
        file=BytesIO(b"fake-image"),
    )

    response = api_main.check_omr(
        image=upload,
        template_id="20q",
        evaluation=None,
        school_id="1",
        exam_id="45",
        require_roll=False,
        pre_rectified=False,
    )

    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["file_id"] == "upload.jpg"
    assert payload["score"] == 3.0
    assert payload["responses"] == {
        "Roll": "01234",
        "q1": "A",
        "q2": "",
    }


@pytest.mark.parametrize(
    ("error_code", "expected_detail"),
    [
        ("markers_not_found", "marker(s) not found"),
        ("multiple_marks", "Multiple marks were detected"),
        ("bubble_grid_not_found", "Unable to align the answer bubble grid"),
    ],
)
def test_check_endpoint_preserves_processing_error_reason(
    monkeypatch,
    error_code,
    expected_detail,
):
    monkeypatch.setattr(
        api_main,
        "entry_point",
        lambda _work_dir, _args: {
            "error_code": error_code,
            "file_id": "upload.jpg",
        },
    )
    upload = UploadFile(
        filename="sheet.jpg",
        file=BytesIO(b"fake-image"),
    )

    with pytest.raises(HTTPException) as exc_info:
        api_main.check_omr(
            image=upload,
            template_id="20q",
            evaluation=None,
            school_id="1",
            exam_id="45",
            require_roll=True,
            pre_rectified=False,
        )

    assert exc_info.value.status_code == 400
    assert expected_detail in exc_info.value.detail


def test_api_fast_path_matches_csv_result(tmp_path, mocker):
    setup_mocker_patches(mocker)

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    image_path = input_dir / "sample.png"
    shutil.copy2(
        "src/tests/test_samples/sample1/sample.png",
        image_path,
    )
    (input_dir / "template.json").write_text(
        json.dumps(TEMPLATE_BOILERPLATE),
        encoding="utf-8",
    )

    legacy_output = tmp_path / "legacy-output"
    fast_output = tmp_path / "fast-output"
    common_args = {
        "debug": False,
        "setLayout": False,
        "autoAlign": False,
        "skip_config_table": True,
        "single_file": str(image_path),
    }

    legacy_result = process_single_file(
        input_dir,
        image_path,
        {
            **common_args,
            "output_dir": str(legacy_output),
        },
    )
    csv_path = next((legacy_output / "Results").glob("Results_*.csv"))
    csv_row = pd.read_csv(csv_path, dtype=str, keep_default_na=False).iloc[0]

    fast_result = process_single_file(
        input_dir,
        image_path,
        {
            **common_args,
            "output_dir": str(fast_output),
            "return_result": True,
        },
    )

    assert legacy_result is None
    assert fast_result is not None
    assert fast_result["file_id"] == csv_row["file_id"]
    assert fast_result["score"] == float(csv_row["score"])
    assert fast_result["responses"] == {
        key: csv_row[key] for key in fast_result["responses"]
    }
    assert (fast_output / "CheckedOMRs" / fast_result["file_id"]).is_file()
    assert not list((fast_output / "Results").glob("Results_*.csv"))
