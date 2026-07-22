import json
import os
from io import BytesIO

import pandas as pd
from starlette.datastructures import UploadFile

os.environ.setdefault("OMR_ALLOW_NO_AUTH", "1")

from api import main as api_main
from api.main import _roll_stem_for_checked_storage, _roll_warning_if_configured


ROLL_TEMPLATE = {
    "customLabels": {
        "Roll": ["roll1", "roll2", "roll3", "roll4", "roll5"],
    },
}


def test_roll_warning_is_returned_for_short_student_code():
    row = pd.Series({"Roll": "33"})

    warning = _roll_warning_if_configured(ROLL_TEMPLATE, row)

    assert warning is not None
    assert warning["code"] == "invalid_roll"
    assert warning["field"] == "Roll"
    assert warning["min_length"] == 4
    assert warning["max_length"] == 5


def test_valid_roll_has_no_warning_and_can_use_by_roll_storage():
    row = pd.Series({"Roll": "01234"})
    responses = {"Roll": "01234"}

    assert _roll_warning_if_configured(ROLL_TEMPLATE, row) is None
    assert _roll_stem_for_checked_storage(responses, ROLL_TEMPLATE) == "01234"


def test_invalid_roll_does_not_use_by_roll_storage():
    assert _roll_stem_for_checked_storage({"Roll": "33"}, ROLL_TEMPLATE) is None
    assert _roll_stem_for_checked_storage({"Roll": ""}, ROLL_TEMPLATE) is None
    assert _roll_stem_for_checked_storage({"Roll": "12AB"}, ROLL_TEMPLATE) is None


def test_check_endpoint_returns_warning_instead_of_rejecting_invalid_roll(monkeypatch):
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
                    "Roll": "33",
                    "q1": "A",
                }
            ]
        ).to_csv(os.path.join(results_dir, "Results_test.csv"), index=False)

    monkeypatch.setattr(api_main, "entry_point", fake_entry_point)
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
    assert payload["warnings"][0]["code"] == "invalid_roll"
