from types import SimpleNamespace

import numpy as np

from src.core import ImageInstanceOps


class FeatureBasedAlignment:
    def __init__(self):
        self.call_count = 0

    def apply_filter(self, image, _file_path):
        self.call_count += 1
        return image + 10


class CropOnMarkers:
    def __init__(self, fail_raw=False):
        self.inputs = []
        self.fail_raw = fail_raw

    def apply_filter(self, image, _file_path):
        self.inputs.append(image.copy())
        if self.fail_raw and int(image[0, 0]) == 0:
            return None
        return image + 1


def make_ops_and_template(monkeypatch, marker_processor):
    config = SimpleNamespace(
        outputs=SimpleNamespace(save_image_level=0),
        dimensions=SimpleNamespace(processing_width=2, processing_height=2),
    )
    ops = ImageInstanceOps(config)
    alignment_processor = FeatureBasedAlignment()
    template = SimpleNamespace(
        pre_processors=[alignment_processor, marker_processor],
    )
    monkeypatch.setattr(
        "src.core.ImageUtils.resize_util",
        lambda source, _width, _height: source.copy(),
    )
    return ops, template, alignment_processor


def test_marker_detection_prefers_image_before_feature_alignment(monkeypatch):
    marker_processor = CropOnMarkers()
    ops, template, alignment_processor = make_ops_and_template(
        monkeypatch,
        marker_processor,
    )
    image = np.zeros((2, 2), dtype=np.uint8)

    result = ops.apply_preprocessors("sheet.jpg", image, template)

    assert len(marker_processor.inputs) == 1
    assert int(marker_processor.inputs[0][0, 0]) == 0
    assert alignment_processor.call_count == 0
    assert int(result[0, 0]) == 1


def test_marker_detection_uses_aligned_fallback_when_raw_detection_fails(
    monkeypatch,
):
    marker_processor = CropOnMarkers(fail_raw=True)
    ops, template, alignment_processor = make_ops_and_template(
        monkeypatch,
        marker_processor,
    )
    image = np.zeros((2, 2), dtype=np.uint8)

    result = ops.apply_preprocessors("sheet.jpg", image, template)

    assert len(marker_processor.inputs) == 2
    assert int(marker_processor.inputs[0][0, 0]) == 0
    assert int(marker_processor.inputs[1][0, 0]) == 10
    assert alignment_processor.call_count == 1
    assert int(result[0, 0]) == 11
