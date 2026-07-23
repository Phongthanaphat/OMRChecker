from types import SimpleNamespace

import numpy as np

from src.core import ImageInstanceOps


def make_roll_bubbles():
    return [
        SimpleNamespace(
            x=20,
            y=20 + digit * 20,
            field_label="roll1",
            field_type="QTYPE_INT",
            field_value=str(digit),
        )
        for digit in range(10)
    ]


def test_inner_darkness_fallback_recovers_faint_roll_digit():
    image = np.full((240, 80), 230, dtype=np.uint8)
    bubbles = make_roll_bubbles()
    marked = bubbles[1]
    image[marked.y + 5 : marked.y + 15, marked.x + 5 : marked.x + 15] = 70

    detected = ImageInstanceOps.detect_int_bubble_by_inner_darkness(
        image,
        bubbles,
        box_w=20,
        box_h=20,
        shift=0,
        detected_bubbles=[],
    )

    assert detected is marked
    assert detected.field_value == "1"


def test_inner_darkness_fallback_does_not_guess_blank_roll_digit():
    image = np.full((240, 80), 230, dtype=np.uint8)
    bubbles = make_roll_bubbles()

    detected = ImageInstanceOps.detect_int_bubble_by_inner_darkness(
        image,
        bubbles,
        box_w=20,
        box_h=20,
        shift=0,
        detected_bubbles=[],
    )

    assert detected is None
