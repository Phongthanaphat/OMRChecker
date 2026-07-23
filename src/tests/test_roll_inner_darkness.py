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


def test_circle_grid_alignment_recovers_affine_roll_grid():
    image = np.full((1500, 900), 235, dtype=np.uint8)
    columns = []
    expected_digits = (1, 2, 4, 3, 4)
    actual_origin = np.array([300.0, 720.0])
    horizontal_step = np.array([66.0, -7.0])
    vertical_step = np.array([-4.0, 63.0])

    for column in range(5):
        bubbles = []
        for digit in range(10):
            bubble = SimpleNamespace(
                x=190 + column * 76,
                y=620 + digit * 72.5,
                field_label=f"roll{column + 1}",
                field_type="QTYPE_INT",
                field_value=str(digit),
            )
            bubbles.append(bubble)
            center = (
                actual_origin
                + column * horizontal_step
                + digit * vertical_step
            )
            center = tuple(np.rint(center).astype(int))
            cv2.circle(image, center, 21, 45, 2)
            if digit == expected_digits[column]:
                cv2.circle(image, center, 11, 55, -1)
        columns.append(bubbles)

    field_block = SimpleNamespace(
        bubble_dimensions=[45, 45],
        traverse_bubbles=columns,
    )
    fitted_centers = ImageInstanceOps.find_roll_grid_centers(image, field_block)

    assert fitted_centers is not None
    detected_digits = []
    for column, bubbles in enumerate(columns):
        for digit, bubble in enumerate(bubbles):
            expected_center = (
                actual_origin
                + column * horizontal_step
                + digit * vertical_step
            )
            assert np.linalg.norm(
                np.array(fitted_centers[id(bubble)]) - expected_center
            ) < 4

        detected = ImageInstanceOps.detect_int_bubble_by_inner_darkness(
            image,
            bubbles,
            box_w=45,
            box_h=45,
            shift=0,
            detected_bubbles=[],
            bubble_centers=fitted_centers,
        )
        assert detected is not None
        detected_digits.append(int(detected.field_value))

    assert detected_digits == list(expected_digits)


def test_circle_grid_alignment_rejects_image_without_roll_grid():
    image = np.full((1500, 900), 235, dtype=np.uint8)
    columns = []
    for column in range(5):
        columns.append(
            [
                SimpleNamespace(
                    x=190 + column * 76,
                    y=620 + digit * 72.5,
                    field_label=f"roll{column + 1}",
                    field_type="QTYPE_INT",
                    field_value=str(digit),
                )
                for digit in range(10)
            ]
        )
    field_block = SimpleNamespace(
        bubble_dimensions=[45, 45],
        traverse_bubbles=columns,
    )

    assert ImageInstanceOps.find_roll_grid_centers(image, field_block) is None


def test_circle_grid_alignment_reads_affine_mcq_rows():
    image = np.full((1500, 900), 235, dtype=np.uint8)
    rows = []
    expected_answers = ("C", "E", "A", "D", "B", "C", "A", "E", "D", "B")
    values = ("A", "B", "C", "D", "E")
    actual_origin = np.array([290.0, 600.0])
    horizontal_step = np.array([62.0, -6.0])
    vertical_step = np.array([-5.0, 61.0])

    for row, expected_answer in enumerate(expected_answers):
        bubbles = []
        for column, value in enumerate(values):
            bubble = SimpleNamespace(
                x=210 + column * 70,
                y=510 + row * 70,
                field_label=f"q{row + 1}",
                field_type="QTYPE_MCQ5",
                field_value=value,
            )
            bubbles.append(bubble)
            center = (
                actual_origin
                + column * horizontal_step
                + row * vertical_step
            )
            center = tuple(np.rint(center).astype(int))
            cv2.circle(image, center, 19, 45, 2)
            if value == expected_answer:
                cv2.circle(image, center, 10, 55, -1)
        rows.append(bubbles)

    field_block = SimpleNamespace(
        name="q01block",
        bubble_dimensions=[42, 42],
        traverse_bubbles=rows,
    )
    fitted_centers = ImageInstanceOps.find_roll_grid_centers(image, field_block)

    assert fitted_centers is not None
    detected_answers = []
    for row, bubbles in enumerate(rows):
        detected = ImageInstanceOps.detect_mcq_bubbles_by_inner_darkness(
            image,
            bubbles,
            fitted_centers,
        )
        assert len(detected) == 1
        detected_answers.append(detected[0].field_value)

        for column, bubble in enumerate(bubbles):
            expected_center = (
                actual_origin
                + column * horizontal_step
                + row * vertical_step
            )
            assert np.linalg.norm(
                np.array(fitted_centers[id(bubble)]) - expected_center
            ) < 4

    assert detected_answers == list(expected_answers)


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
