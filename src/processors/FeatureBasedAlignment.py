"""
Image based feature alignment
Credits: https://www.learnopencv.com/image-alignment-feature-based-using-opencv-c-python/
"""
import cv2
import numpy as np
from collections import OrderedDict

from src.processors.interfaces.ImagePreprocessor import ImagePreprocessor
from src.utils.image import ImageUtils
from src.utils.interaction import InteractionUtils
from src.utils.cache import file_digest, get_positive_int_env, lru_get, lru_put
from src.constants.image_processing import (
    DEFAULT_MAX_FEATURES,
    DEFAULT_GOOD_MATCH_PERCENT
)

_REFERENCE_CACHE_MAX = get_positive_int_env("OMR_REFERENCE_CACHE_MAX", 32)
_REFERENCE_CACHE = OrderedDict()


class FeatureBasedAlignment(ImagePreprocessor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        options = self.options
        config = self.tuning_config

        # process reference image
        self.ref_path = self.relative_dir.joinpath(options["reference"])
        # get options with defaults
        self.max_features = int(options.get("maxFeatures", DEFAULT_MAX_FEATURES))
        self.good_match_percent = options.get("goodMatchPercent", DEFAULT_GOOD_MATCH_PERCENT)
        self.transform_2_d = options.get("2d", False)
        self.orb = cv2.ORB_create(self.max_features)

        reference_digest = file_digest(self.ref_path)
        cache_key = (
            reference_digest,
            int(config.dimensions.processing_width),
            int(config.dimensions.processing_height),
            self.max_features,
        )
        cached = lru_get(_REFERENCE_CACHE, cache_key)
        if cached is None:
            ref_img = cv2.imread(str(self.ref_path), cv2.IMREAD_GRAYSCALE)
            ref_img = ImageUtils.resize_util(
                ref_img,
                config.dimensions.processing_width,
                config.dimensions.processing_height,
            )
            to_keypoints, to_descriptors = self.orb.detectAndCompute(ref_img, None)
            cached = (ref_img, to_keypoints, to_descriptors)
            lru_put(_REFERENCE_CACHE, cache_key, cached, _REFERENCE_CACHE_MAX)

        self.ref_img, self.to_keypoints, self.to_descriptors = cached

    def __str__(self):
        return self.ref_path.name

    def exclude_files(self):
        return [self.ref_path]

    def apply_filter(self, image, _file_path):
        config = self.tuning_config
        # Convert images to grayscale
        # im1Gray = cv2.cvtColor(im1, cv2.COLOR_BGR2GRAY)
        # im2Gray = cv2.cvtColor(im2, cv2.COLOR_BGR2GRAY)

        image = cv2.normalize(image, 0, 255, norm_type=cv2.NORM_MINMAX)

        # Detect ORB features and compute descriptors.
        from_keypoints, from_descriptors = self.orb.detectAndCompute(image, None)

        # Graceful fallback: ภาพไม่มี feature พอ (เช่น ภาพเปล่า/มืด) → คืนภาพเดิม
        # ให้ CropOnMarkers ตัดสินต่อ (ได้ error message เรื่อง marker ที่ชัดกว่า 500)
        if from_descriptors is None or self.to_descriptors is None:
            return image

        # Match features.
        matcher = cv2.DescriptorMatcher_create(
            cv2.DESCRIPTOR_MATCHER_BRUTEFORCE_HAMMING
        )

        # create BFMatcher object (alternate matcher)
        # matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)

        matches = np.array(matcher.match(from_descriptors, self.to_descriptors, None))

        # Sort matches by score
        matches = sorted(matches, key=lambda x: x.distance, reverse=False)

        # Remove not so good matches
        num_good_matches = int(len(matches) * self.good_match_percent)
        matches = matches[:num_good_matches]

        # Draw top matches
        if config.outputs.show_image_level > 2:
            im_matches = cv2.drawMatches(
                image, from_keypoints, self.ref_img, self.to_keypoints, matches, None
            )
            InteractionUtils.show("Aligning", im_matches, resize=True, config=config)

        # Extract location of good matches
        points1 = np.zeros((len(matches), 2), dtype=np.float32)
        points2 = np.zeros((len(matches), 2), dtype=np.float32)

        for i, match in enumerate(matches):
            points1[i, :] = from_keypoints[match.queryIdx].pt
            points2[i, :] = self.to_keypoints[match.trainIdx].pt

        # Graceful fallback: match น้อยเกินกว่าจะหา homography ได้ → คืนภาพเดิม
        if len(matches) < 4:
            return image

        # Find homography
        height, width = self.ref_img.shape
        if self.transform_2_d:
            m, _inliers = cv2.estimateAffine2D(points1, points2)
            if m is None:
                return image
            return cv2.warpAffine(image, m, (width, height))

        # Use homography
        h, _mask = cv2.findHomography(points1, points2, cv2.RANSAC)
        if h is None:
            return image
        return cv2.warpPerspective(image, h, (width, height))
