import json
import os
from io import BytesIO

import pandas as pd
from fastapi import HTTPException
from starlette.datastructures import UploadFile

os.environ.setdefault("OMR_ALLOW_NO_AUTH", "1")

from api import main as api_main
from api.main import (
    _reject_unreliable_roll_if_configured,
    _roll_stem_for_checked_storage,
    _roll_warning_if_configured,
)


ROLL_TEMPLATE = {
    "customLabels": {
        "Roll": ["roll1", "roll2", "roll3", "roll4", "roll5"],
    },
}


def fake_entry_point_with_roll(roll):
    def fake_entry_point(_work_dir, omr_args):
        out_dir = os.fspath(omr_args["output_dir"])
        results_dir = os.path.join(out_dir, "scans", "Results")
        os.makedirs(results_dir, exist_ok=True)
        pd.DataFrame(
            [
                {
                    "file_id": "upload.jpg",
                    "input_path": "upload.jpg",
                    "output_path": "upload.jpg",
                    "score": "1",
                    "Roll": roll,
                    "q1": "A",
                }
            ]
        ).to_csv(os.path.join(results_dir, "Results_test.csv"), index=False)

    return fake_entry_point


def test_roll_warning_is_returned_for_short_student_code():
    row = pd.Series({"Roll": "33"})

    warning = _roll_warning_if_configured(ROLL_TEMPLATE, row)

    assert warning is not None
    assert warning["code"] == "incomplete_roll"
    assert warning["field"] == "Roll"
    assert warning["min_length"] == 2
    assert warning["max_length"] == 5
    assert warning["expected_length"] == 5
    assert warning["detected_length"] == 2


def test_valid_roll_has_no_warning_and_can_use_by_roll_storage():
    row = pd.Series({"Roll": "01234"})
    responses = {"Roll": "01234"}

    assert _roll_warning_if_configured(ROLL_TEMPLATE, row) is None
    assert _roll_stem_for_checked_storage(responses, ROLL_TEMPLATE) == "01234"


def test_invalid_roll_does_not_use_by_roll_storage():
    assert _roll_stem_for_checked_storage({"Roll": "33"}, ROLL_TEMPLATE) is None
    assert _roll_stem_for_checked_storage({"Roll": "0844"}, ROLL_TEMPLATE) is None
    assert _roll_stem_for_checked_storage({"Roll": ""}, ROLL_TEMPLATE) is None
    assert _roll_stem_for_checked_storage({"Roll": "12AB"}, ROLL_TEMPLATE) is None


def test_overlong_roll_is_rejected_as_unreliable():
    row = pd.Series({"Roll": "1234567890"})

    try:
        _reject_unreliable_roll_if_configured(ROLL_TEMPLATE, row)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "Unreliable Roll read" in exc.detail
    else:
        raise AssertionError("Expected HTTPException for overlong Roll")


def test_blank_roll_is_rejected_as_unreliable():
    row = pd.Series({"Roll": ""})

    try:
        _reject_unreliable_roll_if_configured(ROLL_TEMPLATE, row)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "expected 2-5 digits" in exc.detail
    else:
        raise AssertionError("Expected HTTPException for blank Roll")


def test_one_digit_roll_is_rejected_as_unreliable():
    row = pd.Series({"Roll": "7"})

    try:
        _reject_unreliable_roll_if_configured(ROLL_TEMPLATE, row)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "expected 2-5 digits" in exc.detail
    else:
        raise AssertionError("Expected HTTPException for one-digit Roll")


def test_non_digit_roll_is_rejected_as_unreliable():
    row = pd.Series({"Roll": "12AB"})

    try:
        _reject_unreliable_roll_if_configured(ROLL_TEMPLATE, row)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "expected 2-5 digits" in exc.detail
    else:
        raise AssertionError("Expected HTTPException for non-digit Roll")


def test_check_endpoint_returns_warning_instead_of_rejecting_short_roll(monkeypatch):
    monkeypatch.setattr(api_main, "entry_point", fake_entry_point_with_roll("33"))
    upload = UploadFile(
        filename="sheet.jpg",
        file=BytesIO(b"fake-image"),
    )

    response = api_main.check_omr(
        image=upload,
        template_id="20q",
        evaluation=None,
        school_id=None,
        exam_id="45",
        require_roll=True,
    )

    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["responses"]["Roll"] == "33"
    assert payload["warnings"][0]["code"] == "incomplete_roll"


def test_check_endpoint_warns_for_four_digit_roll_on_five_slot_template(monkeypatch):
    monkeypatch.setattr(api_main, "entry_point", fake_entry_point_with_roll("0844"))
    upload = UploadFile(
        filename="sheet.jpg",
        file=BytesIO(b"fake-image"),
    )

    response = api_main.check_omr(
        image=upload,
        template_id="20q",
        evaluation=None,
        school_id=None,
        exam_id="45",
        require_roll=True,
    )

    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["responses"]["Roll"] == "0844"
    assert payload["warnings"][0]["code"] == "incomplete_roll"
    assert payload["warnings"][0]["expected_length"] == 5
    assert payload["warnings"][0]["detected_length"] == 4


def test_check_endpoint_rejects_overlong_roll(monkeypatch):
    monkeypatch.setattr(api_main, "entry_point", fake_entry_point_with_roll("1234567890"))
    upload = UploadFile(
        filename="sheet.jpg",
        file=BytesIO(b"fake-image"),
    )

    try:
        api_main.check_omr(
            image=upload,
            template_id="20q",
            evaluation=None,
            school_id=None,
            exam_id="45",
            require_roll=True,
        )
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "Unreliable Roll read" in exc.detail
    else:
        raise AssertionError("Expected HTTPException for overlong Roll")


def test_check_endpoint_rejects_blank_roll(monkeypatch):
    monkeypatch.setattr(api_main, "entry_point", fake_entry_point_with_roll(""))
    upload = UploadFile(
        filename="sheet.jpg",
        file=BytesIO(b"fake-image"),
    )

    try:
        api_main.check_omr(
            image=upload,
            template_id="20q",
            evaluation=None,
            school_id=None,
            exam_id="45",
            require_roll=True,
        )
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "expected 2-5 digits" in exc.detail
    else:
        raise AssertionError("Expected HTTPException for blank Roll")


def test_check_endpoint_rejects_one_digit_roll(monkeypatch):
    monkeypatch.setattr(api_main, "entry_point", fake_entry_point_with_roll("7"))
    upload = UploadFile(
        filename="sheet.jpg",
        file=BytesIO(b"fake-image"),
    )

    try:
        api_main.check_omr(
            image=upload,
            template_id="20q",
            evaluation=None,
            school_id=None,
            exam_id="45",
            require_roll=True,
        )
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "expected 2-5 digits" in exc.detail
    else:
        raise AssertionError("Expected HTTPException for one-digit Roll")


def test_check_endpoint_rejects_non_digit_roll(monkeypatch):
    monkeypatch.setattr(api_main, "entry_point", fake_entry_point_with_roll("12AB"))
    upload = UploadFile(
        filename="sheet.jpg",
        file=BytesIO(b"fake-image"),
    )

    try:
        api_main.check_omr(
            image=upload,
            template_id="20q",
            evaluation=None,
            school_id=None,
            exam_id="45",
            require_roll=True,
        )
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "expected 2-5 digits" in exc.detail
    else:
        raise AssertionError("Expected HTTPException for non-digit Roll")
