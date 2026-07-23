import shutil

import cv2
import numpy as np

from src.core import ImageInstanceOps
from src.defaults import CONFIG_DEFAULTS
from src.processors.manager import PROCESSOR_MANAGER  # noqa: F401
from src.processors.CropOnMarkers import CropOnMarkers, _MARKER_CACHE
from src.processors.FeatureBasedAlignment import FeatureBasedAlignment, _REFERENCE_CACHE


class _Ops:
    tuning_config = CONFIG_DEFAULTS

    def append_save_img(self, *_args):
        return None


def test_image_instance_save_stack_is_instance_local():
    first = ImageInstanceOps(CONFIG_DEFAULTS)
    second = ImageInstanceOps(CONFIG_DEFAULTS)
    first.save_image_level = 1

    first.append_save_img(1, np.zeros((2, 2), dtype=np.uint8))

    assert len(first.save_img_list[1]) == 1
    assert second.save_img_list[1] == []


def test_marker_cache_reuses_identical_temp_file_contents(tmp_path):
    _MARKER_CACHE.clear()
    source = "templates/50q/omr_marker.jpg"
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    shutil.copy2(source, first_dir / "omr_marker.jpg")
    shutil.copy2(source, second_dir / "omr_marker.jpg")

    first = CropOnMarkers(
        options={"relativePath": "omr_marker.jpg"},
        relative_dir=first_dir,
        image_instance_ops=_Ops(),
    )
    second = CropOnMarkers(
        options={"relativePath": "omr_marker.jpg"},
        relative_dir=second_dir,
        image_instance_ops=_Ops(),
    )

    assert first.marker.shape == second.marker.shape
    assert len(_MARKER_CACHE) == 1


def test_axis_aligned_marker_crop_does_not_warp_pixels():
    image = np.arange(100 * 80, dtype=np.int32).reshape((100, 80))
    centres = np.array(
        [
            [10, 20],
            [70, 22],
            [68, 82],
            [12, 80],
        ],
        dtype=np.float32,
    )

    cropped = CropOnMarkers.crop_axis_aligned(image, centres)

    assert cropped is not None
    assert cropped.shape == (60, 58)
    assert np.array_equal(cropped, image[21:81, 11:69])
    assert cropped.base is None


def test_axis_aligned_geometry_rejects_skewed_marker_grid():
    processor = object.__new__(CropOnMarkers)
    processor.max_axis_tilt_degrees = 3.0
    processor.max_axis_side_ratio = 1.06
    skewed_centres = np.array(
        [
            [10, 20],
            [70, 30],
            [68, 82],
            [12, 80],
        ],
        dtype=np.float32,
    )

    geometry = processor.marker_geometry(skewed_centres)

    assert processor.is_axis_geometry_reliable(geometry) is False


def test_reference_cache_reuses_identical_temp_file_contents(tmp_path):
    _REFERENCE_CACHE.clear()
    source = "templates/50q/reference.png"
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    shutil.copy2(source, first_dir / "reference.png")
    shutil.copy2(source, second_dir / "reference.png")

    first = FeatureBasedAlignment(
        options={"reference": "reference.png"},
        relative_dir=first_dir,
        image_instance_ops=_Ops(),
    )
    second = FeatureBasedAlignment(
        options={"reference": "reference.png"},
        relative_dir=second_dir,
        image_instance_ops=_Ops(),
    )

    assert first.ref_img.shape == second.ref_img.shape
    assert len(_REFERENCE_CACHE) == 1


def test_api_image_dimensions_from_header_png_and_jpeg(tmp_path, monkeypatch):
    monkeypatch.setenv("OMR_ALLOW_NO_AUTH", "1")
    from api.main import _image_dimensions_from_header

    png_path = tmp_path / "image.png"
    jpg_path = tmp_path / "image.jpg"
    image = np.zeros((17, 23, 3), dtype=np.uint8)
    cv2.imwrite(str(png_path), image)
    cv2.imwrite(str(jpg_path), image)

    assert _image_dimensions_from_header(png_path.read_bytes()[:64], ".png") == (
        23,
        17,
    )
    assert _image_dimensions_from_header(jpg_path.read_bytes()[:512], ".jpg") == (
        23,
        17,
    )
