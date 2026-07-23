from types import SimpleNamespace

import cv2
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


def test_inner_darkness_fallback_uses_local_contrast_under_uneven_shadow():
    image = np.full((240, 80), 230, dtype=np.uint8)
    bubbles = make_roll_bubbles()

    # Darken lower rows progressively to simulate a page shadow.
    for row in range(image.shape[0]):
        image[row, :] = max(150, 230 - row // 3)

    marked = bubbles[2]
    image[
        marked.y + 5 : marked.y + 15,
        marked.x + 4 : marked.x + 12,
    ] = 95

    detected = ImageInstanceOps.detect_int_bubble_by_inner_darkness(
        image,
        bubbles,
        box_w=20,
        box_h=20,
        shift=0,
        detected_bubbles=[],
    )

    assert detected is marked


def test_inner_darkness_fallback_does_not_guess_from_shadow_alone():
    image = np.full((240, 80), 230, dtype=np.uint8)
    bubbles = make_roll_bubbles()

    for row in range(image.shape[0]):
        image[row, :] = max(150, 230 - row // 3)

    detected = ImageInstanceOps.detect_int_bubble_by_inner_darkness(
        image,
        bubbles,
        box_w=20,
        box_h=20,
        shift=0,
        detected_bubbles=[],
    )

    assert detected is None


def test_roll_grid_alignment_recovers_one_shared_translation():
    row_levels = np.linspace(235, 190, 820, dtype=np.uint8)
    image = np.repeat(row_levels[:, None], 440, axis=1)
    columns = []
    expected_digits = (1, 2, 3, 8, 9)
    for column in range(5):
        bubbles = []
        for digit in range(10):
            bubble = SimpleNamespace(
                x=30 + column * 76,
                y=30 + digit * 72,
                field_label=f"roll{column + 1}",
                field_type="QTYPE_INT",
                field_value=str(digit),
            )
            bubbles.append(bubble)
            center = (bubble.x + 22 - 5, bubble.y + 22 + 6)
            cv2.circle(image, center, 18, 40, 2)
            if digit == expected_digits[column]:
                cv2.circle(image, center, 12, 65, -1)
        columns.append(bubbles)

    field_block = SimpleNamespace(
        bubble_dimensions=[45, 45],
        traverse_bubbles=columns,
    )

    offset = ImageInstanceOps.find_roll_grid_offset(
        image,
        field_block,
        max_offset=10,
    )

    assert abs(offset[0] - (-5)) <= 1
    assert abs(offset[1] - 6) <= 1
    detected_digits = []
    for bubbles in columns:
        detected = ImageInstanceOps.detect_int_bubble_by_inner_darkness(
            image,
            bubbles,
            box_w=45,
            box_h=45,
            shift=offset[0],
            shift_y=offset[1],
            detected_bubbles=[],
        )
        assert detected is not None
        detected_digits.append(int(detected.field_value))

    assert detected_digits == list(expected_digits)


def test_inner_darkness_does_not_guess_between_two_similar_marks():
    image = np.full((240, 80), 230, dtype=np.uint8)
    bubbles = make_roll_bubbles()
    for marked in (bubbles[1], bubbles[2]):
        image[
            marked.y + 5 : marked.y + 15,
            marked.x + 5 : marked.x + 15,
        ] = 70

    detected = ImageInstanceOps.detect_int_bubble_by_inner_darkness(
        image,
        bubbles,
        box_w=20,
        box_h=20,
        shift=0,
        detected_bubbles=[],
    )

    assert detected is None
