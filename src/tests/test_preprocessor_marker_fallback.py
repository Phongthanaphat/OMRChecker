from types import SimpleNamespace

import numpy as np

from src.core import ImageInstanceOps


class FeatureBasedAlignment:
    def apply_filter(self, image, _file_path):
        return image + 10


class CropOnMarkers:
    def __init__(self):
        self.inputs = []

    def apply_filter(self, image, _file_path):
        self.inputs.append(image.copy())
        if int(image[0, 0]) == 10:
            return None
        return image + 1


def test_marker_detection_retries_image_before_feature_alignment(monkeypatch):
    config = SimpleNamespace(
        outputs=SimpleNamespace(save_image_level=0),
        dimensions=SimpleNamespace(processing_width=2, processing_height=2),
    )
    ops = ImageInstanceOps(config)
    marker_processor = CropOnMarkers()
    template = SimpleNamespace(
        pre_processors=[FeatureBasedAlignment(), marker_processor],
    )
    image = np.zeros((2, 2), dtype=np.uint8)
    monkeypatch.setattr(
        "src.core.ImageUtils.resize_util",
        lambda source, _width, _height: source.copy(),
    )

    result = ops.apply_preprocessors("sheet.jpg", image, template)

    assert len(marker_processor.inputs) == 2
    assert int(marker_processor.inputs[0][0, 0]) == 10
    assert int(marker_processor.inputs[1][0, 0]) == 0
    assert int(result[0, 0]) == 1
