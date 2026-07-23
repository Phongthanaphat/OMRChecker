from collections import OrderedDict
from itertools import product
from math import atan2, degrees
import os
from time import perf_counter

import cv2
import numpy as np

from src.constants.image_processing import (
    DEFAULT_BLACK_COLOR,
    DEFAULT_BORDER_REMOVE,
    DEFAULT_GAUSSIAN_BLUR_PARAMS_MARKER,
    DEFAULT_LINE_WIDTH,
    DEFAULT_NORMALIZE_PARAMS,
    DEFAULT_WHITE_COLOR,
    ERODE_RECT_COLOR,
    EROSION_PARAMS,
    MARKER_RECTANGLE_COLOR,
    NORMAL_RECT_COLOR,
    QUADRANT_DIVISION,
)
from src.logger import logger
from src.processors.interfaces.ImagePreprocessor import ImagePreprocessor
from src.utils.image import ImageUtils
from src.utils.interaction import InteractionUtils
from src.utils.numeric import to_scalar
from src.utils.cache import file_digest, get_positive_int_env, lru_get, lru_put

_MARKER_CACHE_MAX = get_positive_int_env("OMR_MARKER_CACHE_MAX", 32)
_MARKER_CACHE = OrderedDict()


class CropOnMarkers(ImagePreprocessor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        config = self.tuning_config
        marker_ops = self.options
        self.threshold_circles = []
        # img_utils = ImageUtils()

        # options with defaults
        self.marker_path = os.path.join(
            self.relative_dir, marker_ops.get("relativePath", "omr_marker.jpg")
        )
        # ต้องเจอ marker ชัดทั้ง 4 มุม ถ้ามุมใดมุมหนึ่งไม่ถึง threshold = ไม่ตรวจ (return None)
        # ค่า 0.5: เฉพาะกระดาษ OMR จริงที่มีวง marker ชัดถึงผ่าน; รูปอื่น (ถ่ายคน ฯลฯ) จะไม่ผ่าน
        self.min_matching_threshold = marker_ops.get("min_matching_threshold", 0.5)
        self.min_quadrant_matching_threshold = marker_ops.get(
            "min_quadrant_matching_threshold",
            self.min_matching_threshold,
        )
        # ความต่างของความเข้ม match ระหว่าง 4 มุมต้องไม่เกินนี้ (ให้ครบ 4 มุมเหมือนกัน)
        self.max_matching_variation = marker_ops.get("max_matching_variation", 0.35)
        self.marker_rescale_range = tuple(
            int(r) for r in marker_ops.get("marker_rescale_range", (35, 100))
        )
        self.marker_rescale_steps = int(marker_ops.get("marker_rescale_steps", 10))
        self.marker_rescale_fallback = bool(marker_ops.get("marker_rescale_fallback", True))
        self.fallback_marker_rescale_range = tuple(
            int(r) for r in marker_ops.get("fallback_marker_rescale_range", (35, 100))
        )
        self.fallback_marker_rescale_steps = int(marker_ops.get("fallback_marker_rescale_steps", 10))
        self.apply_erode_subtract = marker_ops.get("apply_erode_subtract", True)
        self.allow_quadrant_scale_fallback = marker_ops.get(
            "allow_quadrant_scale_fallback", True
        )
        self.crop_mode = marker_ops.get("crop_mode", "perspective")
        self.max_axis_tilt_degrees = float(
            marker_ops.get("max_axis_tilt_degrees", 3.0)
        )
        self.max_axis_side_ratio = float(
            marker_ops.get("max_axis_side_ratio", 1.06)
        )
        self.max_axis_marker_inset_fraction = float(
            marker_ops.get("max_axis_marker_inset_fraction", 0.22)
        )
        self.enable_shadow_fallback = bool(
            marker_ops.get(
                "enable_shadow_fallback",
                self.crop_mode == "axis_aligned",
            )
        )
        self.shadow_dark_median_threshold = float(
            marker_ops.get("shadow_dark_median_threshold", 155)
        )
        self.shadow_retry_median_threshold = float(
            marker_ops.get("shadow_retry_median_threshold", 180)
        )
        self.shadow_quadrant_spread_threshold = float(
            marker_ops.get("shadow_quadrant_spread_threshold", 55)
        )
        self.shadow_retry_quadrant_spread_threshold = float(
            marker_ops.get("shadow_retry_quadrant_spread_threshold", 35)
        )
        self.shadow_quadrant_mean_threshold = float(
            marker_ops.get("shadow_quadrant_mean_threshold", 165)
        )
        self.shadow_retry_quadrant_mean_threshold = float(
            marker_ops.get("shadow_retry_quadrant_mean_threshold", 175)
        )
        self.used_shadow_correction = False
        self.marker = self.load_marker(marker_ops, config)

    def __str__(self):
        return self.marker_path

    def exclude_files(self):
        return [self.marker_path]

    def apply_filter(self, image, file_path):
        config = self.tuning_config
        image_instance_ops = self.image_instance_ops
        self.used_shadow_correction = False
        if self.enable_shadow_fallback:
            metrics = self.shadow_metrics(image)
            if self.should_correct_shadow_first(metrics):
                image = self.correct_uneven_illumination(
                    image,
                    metrics,
                    trigger="dark_first_pass",
                )
                self.used_shadow_correction = True
        image_eroded_sub = ImageUtils.normalize_util(
            image
            if self.apply_erode_subtract
            else (
                image
                - cv2.erode(
                    image,
                    kernel=np.ones(EROSION_PARAMS["kernel_size"]),
                    iterations=EROSION_PARAMS["iterations"],
                )
            )
        )
        # Quads on warped image
        quads = {}
        h1, w1 = image_eroded_sub.shape[:2]
        midh, midw = (
            h1 // QUADRANT_DIVISION["height_factor"],
            w1 // QUADRANT_DIVISION["width_factor"],
        )
        origins = [[0, 0], [midw, 0], [0, midh], [midw, midh]]
        quads[0] = image_eroded_sub[0:midh, 0:midw]
        quads[1] = image_eroded_sub[0:midh, midw:w1]
        quads[2] = image_eroded_sub[midh:h1, 0:midw]
        quads[3] = image_eroded_sub[midh:h1, midw:w1]

        # Draw Quadlines
        image_eroded_sub[:, midw : midw + 2] = DEFAULT_WHITE_COLOR
        image_eroded_sub[midh : midh + 2, :] = DEFAULT_WHITE_COLOR

        used_fallback = False
        quadrant_matches = None
        best_scale, all_max_t = self.getBestMatch(image_eroded_sub)
        if (
            best_scale is None
            and self.marker_rescale_fallback
            and (
                self.marker_rescale_range != self.fallback_marker_rescale_range
                or self.marker_rescale_steps != self.fallback_marker_rescale_steps
            )
        ):
            logger.info(
                "Marker fast scale search failed; retrying fallback range:",
                self.fallback_marker_rescale_range,
            )
            used_fallback = True
            best_scale, all_max_t = self.getBestMatch(
                image_eroded_sub,
                self.fallback_marker_rescale_range,
                self.fallback_marker_rescale_steps,
            )
        if best_scale is None:
            if self.allow_quadrant_scale_fallback:
                quadrant_matches = self.get_quadrant_matches(quads)
            if quadrant_matches is not None:
                used_fallback = True
                all_max_t = max(match["score"] for match in quadrant_matches)
                print(
                    "[OMR marker scale] "
                    f"mode=quadrant_independent "
                    f"match={round(float(all_max_t), 4)}",
                    flush=True,
                )
            else:
                print(
                    "[OMR marker failure] "
                    f"reason=scale_search "
                    f"best_match={round(float(all_max_t), 4)} "
                    f"min_threshold={self.min_matching_threshold} "
                    f"used_fallback={used_fallback}",
                    flush=True,
                )
                if config.outputs.show_image_level >= 1:
                    InteractionUtils.show("Quads", image_eroded_sub, config=config)
                return None

        if quadrant_matches is None:
            optimal_marker = ImageUtils.resize_util_h(
                self.marker, u_height=int(self.marker.shape[0] * best_scale)
            )
            _h, w = optimal_marker.shape[:2]
        else:
            optimal_marker = None
            _h, w = 0, 0

        sum_t, max_t = 0, 0
        match_contexts = []
        quarter_match_log = "Matching Marker:  "
        for k in range(0, 4):
            used_local_scale = False
            if quadrant_matches is not None:
                match = quadrant_matches[k]
                res = match["res"]
                max_t = match["score"]
                _h = match["height"]
                w = match["width"]
                used_local_scale = True
            else:
                res = cv2.matchTemplate(quads[k], optimal_marker, cv2.TM_CCOEFF_NORMED)
                max_t = res.max()
                match_failed = (
                    max_t < self.min_quadrant_matching_threshold
                    or abs(to_scalar(all_max_t) - to_scalar(max_t)) >= self.max_matching_variation
                )
                if match_failed and self.allow_quadrant_scale_fallback:
                    local_match = self.get_quadrant_match(quads[k])
                    if local_match is not None:
                        match = local_match
                        res = match["res"]
                        max_t = match["score"]
                        _h = match["height"]
                        w = match["width"]
                        used_local_scale = True

            quarter_match_log += f"Quarter{str(k + 1)}: {str(round(max_t, 3))}\t"
            if (
                max_t < self.min_quadrant_matching_threshold
                or (
                    not used_local_scale
                    and abs(to_scalar(all_max_t) - to_scalar(max_t)) >= self.max_matching_variation
                )
            ):
                print(
                    "[OMR marker failure] "
                    f"reason=quad_threshold "
                    f"quad={k + 1} "
                    f"quad_match={round(float(max_t), 4)} "
                    f"best_match={round(float(all_max_t), 4)} "
                    f"min_threshold={self.min_quadrant_matching_threshold} "
                    f"max_variation={self.max_matching_variation}",
                    flush=True,
                )
                logger.error(
                    file_path,
                    "\nError: No circle found in Quad",
                    k + 1,
                    "\n\t min_matching_threshold",
                    self.min_matching_threshold,
                    "\t max_matching_variation",
                    self.max_matching_variation,
                    "\t max_t",
                    max_t,
                    "\t all_max_t",
                    all_max_t,
                )
                if config.outputs.show_image_level >= 1:
                    InteractionUtils.show(
                        f"No markers: {file_path}",
                        image_eroded_sub,
                        0,
                        config=config,
                    )
                    InteractionUtils.show(
                        f"res_Q{str(k + 1)} ({str(max_t)})",
                        res,
                        1,
                        config=config,
                    )
                return None

            match_contexts.append(
                {
                    "res": res,
                    "origin": origins[k],
                    "height": _h,
                    "width": w,
                }
            )
            sum_t += max_t

        if self.crop_mode == "axis_aligned":
            selected_candidates = self.select_axis_aligned_marker_candidates(
                match_contexts,
                image_eroded_sub.shape,
            )
            if selected_candidates is None:
                print(
                    "[OMR marker failure] "
                    "reason=no_consistent_axis_rectangle",
                    flush=True,
                )
                return None
        else:
            selected_candidates = [
                self.marker_candidates(context, limit=1)[0]
                for context in match_contexts
            ]

        centres = []
        for candidate in selected_candidates:
            pt = candidate["top_left"]
            w = candidate["width"]
            _h = candidate["height"]
            image = cv2.rectangle(
                image,
                tuple(pt),
                (pt[0] + w, pt[1] + _h),
                MARKER_RECTANGLE_COLOR,
                DEFAULT_LINE_WIDTH,
            )
            image_eroded_sub = cv2.rectangle(
                image_eroded_sub,
                tuple(pt),
                (pt[0] + w, pt[1] + _h),
                ERODE_RECT_COLOR if self.apply_erode_subtract else NORMAL_RECT_COLOR,
                4,
            )
            centres.append([pt[0] + w / 2, pt[1] + _h / 2])

        logger.info(quarter_match_log)
        logger.info(f"Optimal Scale: {best_scale}")
        if quadrant_matches is None:
            print(
                "[OMR marker scale] "
                f"best_scale={best_scale} "
                f"match={round(float(all_max_t), 4)} "
                f"used_fallback={used_fallback}",
                flush=True,
            )
        # analysis data
        self.threshold_circles.append(sum_t / 4)

        ordered_centres = ImageUtils.order_points(
            np.asarray(centres, dtype=np.float32)
        )
        marker_geometry = self.marker_geometry(ordered_centres)
        self.log_marker_geometry(ordered_centres, marker_geometry)
        if self.crop_mode == "axis_aligned":
            if not self.is_axis_geometry_reliable(marker_geometry):
                logger.error(
                    "Pre-rectified marker geometry is not axis-aligned enough; "
                    "refusing to read a potentially distorted sheet."
                )
                return None
            image = self.crop_axis_aligned(image, ordered_centres)
            if image is None:
                logger.error("Axis-aligned marker crop produced an invalid image region.")
                return None
        else:
            image = ImageUtils.four_point_transform(image, ordered_centres)
        # appendSaveImg(1,image_eroded_sub)
        # appendSaveImg(1,image_norm)

        image_instance_ops.append_save_img(2, image_eroded_sub)
        # Debugging image -
        # res = cv2.matchTemplate(image_eroded_sub,optimal_marker,cv2.TM_CCOEFF_NORMED)
        # res[ : , midw:midw+2] = 255
        # res[ midh:midh+2, : ] = 255
        # show("Markers Matching",res)
        if config.outputs.show_image_level >= 2 and config.outputs.show_image_level < 4:
            image_eroded_sub = ImageUtils.resize_util_h(
                image_eroded_sub, image.shape[0]
            )
            image_eroded_sub[:, -DEFAULT_BORDER_REMOVE:] = DEFAULT_BLACK_COLOR
            h_stack = np.hstack((image_eroded_sub, image))
            InteractionUtils.show(
                f"Warped: {file_path}",
                ImageUtils.resize_util(
                    h_stack, int(config.dimensions.display_width * 1.6)
                ),
                0,
                0,
                [0, 0],
                config=config,
            )
        # iterations : Tuned to 2.
        # image_eroded_sub = image_norm - cv2.erode(image_norm, kernel=np.ones((5,5)),iterations=2)
        return image

    @staticmethod
    def shadow_metrics(image):
        """Measure darkness and uneven lighting cheaply on a sampled image."""
        sampled = image[::4, ::4]
        height, width = sampled.shape[:2]
        quadrants = (
            sampled[: height // 2, : width // 2],
            sampled[: height // 2, width // 2 :],
            sampled[height // 2 :, : width // 2],
            sampled[height // 2 :, width // 2 :],
        )
        quadrant_means = [float(np.mean(quadrant)) for quadrant in quadrants]
        return {
            "median": float(np.median(sampled)),
            "min_quadrant_mean": min(quadrant_means),
            "quadrant_spread": max(quadrant_means) - min(quadrant_means),
        }

    def should_correct_shadow_first(self, metrics):
        return (
            metrics["median"] < self.shadow_dark_median_threshold
            or (
                metrics["quadrant_spread"]
                >= self.shadow_quadrant_spread_threshold
                and metrics["min_quadrant_mean"]
                < self.shadow_quadrant_mean_threshold
            )
        )

    def should_retry_with_shadow_correction(self, image):
        if not self.enable_shadow_fallback or self.used_shadow_correction:
            return False
        metrics = self.shadow_metrics(image)
        return (
            metrics["median"] < self.shadow_retry_median_threshold
            or (
                metrics["quadrant_spread"]
                >= self.shadow_retry_quadrant_spread_threshold
                and metrics["min_quadrant_mean"]
                < self.shadow_retry_quadrant_mean_threshold
            )
        )

    @staticmethod
    def correct_uneven_illumination(
        image,
        metrics,
        *,
        trigger,
    ):
        """Flatten broad shadows while preserving the marker's local strokes."""
        started_at = perf_counter()
        height, width = image.shape[:2]
        reduced = cv2.resize(
            image,
            (max(1, width // 2), max(1, height // 2)),
            interpolation=cv2.INTER_AREA,
        )
        reduced_background = cv2.GaussianBlur(reduced, (0, 0), sigmaX=12.5)
        background = cv2.resize(
            reduced_background,
            (width, height),
            interpolation=cv2.INTER_LINEAR,
        )
        divided = cv2.divide(image, background, scale=245)
        corrected = cv2.createCLAHE(
            clipLimit=2.0,
            tileGridSize=(8, 8),
        ).apply(divided)
        print(
            "[OMR marker illumination] "
            f"trigger={trigger} "
            f"median={metrics['median']:.2f} "
            f"min_quadrant_mean={metrics['min_quadrant_mean']:.2f} "
            f"quadrant_spread={metrics['quadrant_spread']:.2f} "
            f"correction_ms={(perf_counter() - started_at) * 1000:.2f}",
            flush=True,
        )
        return corrected

    def apply_shadow_fallback(self, image, file_path):
        metrics = self.shadow_metrics(image)
        corrected = self.correct_uneven_illumination(
            image,
            metrics,
            trigger="normal_detection_failed",
        )
        result = self.apply_filter(corrected, file_path)
        self.used_shadow_correction = True
        return result

    def marker_candidates(
        self,
        match_context,
        limit=6,
        *,
        expected_corner=None,
        image_shape=None,
    ):
        """Return distinct local maxima from one quadrant's template response."""
        response = match_context["res"].copy()
        origin_x, origin_y = match_context["origin"]
        marker_h = int(match_context["height"])
        marker_w = int(match_context["width"])
        if expected_corner is not None and image_shape is not None:
            image_height, image_width = image_shape[:2]
            inset_x = image_width * self.max_axis_marker_inset_fraction
            inset_y = image_height * self.max_axis_marker_inset_fraction
            center_offset_x = origin_x + marker_w / 2
            center_offset_y = origin_y + marker_h / 2

            if expected_corner in (0, 2):
                last_x = int(np.floor(inset_x - center_offset_x))
                response[:, max(0, last_x + 1) :] = -1
            else:
                first_x = int(np.ceil(image_width - inset_x - center_offset_x))
                response[:, : max(0, first_x)] = -1

            if expected_corner in (0, 1):
                last_y = int(np.floor(inset_y - center_offset_y))
                response[max(0, last_y + 1) :, :] = -1
            else:
                first_y = int(np.ceil(image_height - inset_y - center_offset_y))
                response[: max(0, first_y), :] = -1

        suppression_radius = max(2, int(round(max(marker_h, marker_w) * 0.75)))
        candidates = []

        for rank in range(1, limit + 1):
            _, score, _, location = cv2.minMaxLoc(response)
            if score < self.min_quadrant_matching_threshold:
                break
            local_x, local_y = location
            top_left = [local_x + origin_x, local_y + origin_y]
            candidates.append(
                {
                    "rank": rank,
                    "score": float(score),
                    "top_left": top_left,
                    "center": [
                        top_left[0] + marker_w / 2,
                        top_left[1] + marker_h / 2,
                    ],
                    "height": marker_h,
                    "width": marker_w,
                }
            )
            cv2.circle(
                response,
                location,
                suppression_radius,
                -1.0,
                thickness=-1,
            )

        return candidates

    def select_axis_aligned_marker_candidates(self, match_contexts, image_shape):
        """Choose four matches that form one large, straight marker rectangle."""
        candidate_groups = [
            self.marker_candidates(
                context,
                expected_corner=index,
                image_shape=image_shape,
            )
            for index, context in enumerate(match_contexts)
        ]
        if any(not candidates for candidates in candidate_groups):
            return None

        top_candidates = [candidates[0] for candidates in candidate_groups]
        top_result = self.score_axis_marker_selection(
            top_candidates,
            image_shape,
        )
        if top_result is not None:
            top_score, top_geometry = top_result
            self.log_marker_candidate_selection(
                top_candidates,
                [1, 1, 1, 1],
                top_score,
                top_geometry,
            )
            return top_candidates

        image_height, image_width = image_shape[:2]
        best_selection = None
        best_score = float("-inf")
        best_geometry = None

        # Context order is top-left, top-right, bottom-left, bottom-right.
        for tl, tr, bl, br in product(*candidate_groups):
            selection = [tl, tr, bl, br]
            result = self.score_axis_marker_selection(
                selection,
                (image_height, image_width),
            )
            if result is None:
                continue
            selection_score, geometry = result
            if selection_score > best_score:
                best_score = selection_score
                best_selection = selection
                best_geometry = geometry

        if best_selection is not None:
            self.log_marker_candidate_selection(
                best_selection,
                [len(group) for group in candidate_groups],
                best_score,
                best_geometry,
            )
        return best_selection

    def score_axis_marker_selection(self, selection, image_shape):
        tl, tr, bl, br = selection
        ordered_centres = np.asarray(
            [tl["center"], tr["center"], br["center"], bl["center"]],
            dtype=np.float32,
        )
        geometry = self.marker_geometry(ordered_centres)
        if not self.is_axis_geometry_reliable(geometry):
            return None

        image_height, image_width = image_shape[:2]
        top_width = float(np.linalg.norm(ordered_centres[1] - ordered_centres[0]))
        bottom_width = float(np.linalg.norm(ordered_centres[2] - ordered_centres[3]))
        left_height = float(np.linalg.norm(ordered_centres[3] - ordered_centres[0]))
        right_height = float(np.linalg.norm(ordered_centres[2] - ordered_centres[1]))
        width_fraction = min(top_width, bottom_width) / max(image_width, 1)
        height_fraction = min(left_height, right_height) / max(image_height, 1)
        if width_fraction < 0.45 or height_fraction < 0.45:
            return None

        match_score = sum(candidate["score"] for candidate in selection) / 4
        area_score = width_fraction * height_fraction
        tilt_penalty = sum(
            geometry[key]
            for key in (
                "top_tilt",
                "bottom_tilt",
                "left_tilt",
                "right_tilt",
            )
        )
        ratio_penalty = geometry["width_ratio"] + geometry["height_ratio"] - 2
        selection_score = (
            match_score
            + 0.35 * area_score
            - 0.01 * tilt_penalty
            - 0.5 * ratio_penalty
        )
        return selection_score, geometry

    @staticmethod
    def log_marker_candidate_selection(
        selection,
        candidate_counts,
        selection_score,
        geometry,
    ):
        selected_ranks = ",".join(str(item["rank"]) for item in selection)
        selected_scores = ",".join(
            f"{item['score']:.4f}"
            for item in selection
        )
        print(
            "[OMR marker candidate selection] "
            f"candidate_counts={','.join(str(count) for count in candidate_counts)} "
            f"selected_ranks={selected_ranks} "
            f"match_scores={selected_scores} "
            f"selection_score={selection_score:.4f} "
            f"width_ratio={geometry['width_ratio']:.4f} "
            f"height_ratio={geometry['height_ratio']:.4f}",
            flush=True,
        )

    @staticmethod
    def _horizontal_tilt(first, second):
        angle = abs(degrees(atan2(second[1] - first[1], second[0] - first[0])))
        return min(angle, abs(180 - angle))

    @staticmethod
    def _vertical_tilt(first, second):
        angle = abs(degrees(atan2(second[1] - first[1], second[0] - first[0])))
        return abs(90 - min(angle, abs(180 - angle)))

    def marker_geometry(self, ordered_centres):
        tl, tr, br, bl = ordered_centres
        top_width = float(np.linalg.norm(tr - tl))
        bottom_width = float(np.linalg.norm(br - bl))
        left_height = float(np.linalg.norm(bl - tl))
        right_height = float(np.linalg.norm(br - tr))
        width_ratio = max(top_width, bottom_width) / max(
            min(top_width, bottom_width),
            1.0,
        )
        height_ratio = max(left_height, right_height) / max(
            min(left_height, right_height),
            1.0,
        )
        return {
            "top_tilt": self._horizontal_tilt(tl, tr),
            "bottom_tilt": self._horizontal_tilt(bl, br),
            "left_tilt": self._vertical_tilt(tl, bl),
            "right_tilt": self._vertical_tilt(tr, br),
            "width_ratio": width_ratio,
            "height_ratio": height_ratio,
        }

    def is_axis_geometry_reliable(self, geometry):
        return (
            max(
                geometry["top_tilt"],
                geometry["bottom_tilt"],
                geometry["left_tilt"],
                geometry["right_tilt"],
            )
            <= self.max_axis_tilt_degrees
            and geometry["width_ratio"] <= self.max_axis_side_ratio
            and geometry["height_ratio"] <= self.max_axis_side_ratio
        )

    def log_marker_geometry(self, ordered_centres, geometry):
        centres_text = ";".join(
            f"{round(float(point[0]), 1)},{round(float(point[1]), 1)}"
            for point in ordered_centres
        )
        axis_geometry_valid = (
            self.is_axis_geometry_reliable(geometry)
            if self.crop_mode == "axis_aligned"
            else "-"
        )
        print(
            "[OMR marker geometry] "
            f"crop_mode={self.crop_mode} "
            f"centres={centres_text} "
            f"top_tilt_deg={round(geometry['top_tilt'], 2)} "
            f"bottom_tilt_deg={round(geometry['bottom_tilt'], 2)} "
            f"left_tilt_deg={round(geometry['left_tilt'], 2)} "
            f"right_tilt_deg={round(geometry['right_tilt'], 2)} "
            f"width_ratio={round(geometry['width_ratio'], 4)} "
            f"height_ratio={round(geometry['height_ratio'], 4)} "
            f"axis_geometry_valid={axis_geometry_valid}",
            flush=True,
        )

    @staticmethod
    def crop_axis_aligned(image, ordered_centres):
        """Crop to marker centres without applying another perspective transform."""
        tl, tr, br, bl = ordered_centres
        image_height, image_width = image.shape[:2]
        left = int(round((float(tl[0]) + float(bl[0])) / 2))
        right = int(round((float(tr[0]) + float(br[0])) / 2))
        top = int(round((float(tl[1]) + float(tr[1])) / 2))
        bottom = int(round((float(bl[1]) + float(br[1])) / 2))

        left = max(0, min(image_width - 1, left))
        right = max(1, min(image_width, right))
        top = max(0, min(image_height - 1, top))
        bottom = max(1, min(image_height, bottom))
        if right - left < 10 or bottom - top < 10:
            return None
        return image[top:bottom, left:right].copy()

    def load_marker(self, marker_ops, config):
        if not os.path.exists(self.marker_path):
            logger.error(
                "Marker not found at path provided in template:",
                self.marker_path,
            )
            exit(31)

        marker_digest = file_digest(self.marker_path)
        cache_key = (
            marker_digest,
            int(config.dimensions.processing_width),
            int(marker_ops.get("sheetToMarkerWidthRatio", 0) or 0),
            bool(self.apply_erode_subtract),
        )
        cached = lru_get(_MARKER_CACHE, cache_key)
        if cached is not None:
            return cached.copy()

        marker = cv2.imread(self.marker_path, cv2.IMREAD_GRAYSCALE)

        if "sheetToMarkerWidthRatio" in marker_ops:
            marker = ImageUtils.resize_util(
                marker,
                config.dimensions.processing_width
                / int(marker_ops["sheetToMarkerWidthRatio"]),
            )
        marker = cv2.GaussianBlur(
            marker,
            DEFAULT_GAUSSIAN_BLUR_PARAMS_MARKER["kernel_size"],
            DEFAULT_GAUSSIAN_BLUR_PARAMS_MARKER["sigma_x"],
        )
        marker = cv2.normalize(
            marker,
            None,
            alpha=DEFAULT_NORMALIZE_PARAMS["alpha"],
            beta=DEFAULT_NORMALIZE_PARAMS["beta"],
            norm_type=cv2.NORM_MINMAX,
        )

        if self.apply_erode_subtract:
            marker -= cv2.erode(
                marker,
                kernel=np.ones(EROSION_PARAMS["kernel_size"]),
                iterations=EROSION_PARAMS["iterations"],
            )

        lru_put(_MARKER_CACHE, cache_key, marker.copy(), _MARKER_CACHE_MAX)
        return marker

    def get_quadrant_match(self, quad):
        match = self.find_best_marker_match(
            quad,
            self.marker_rescale_range,
            self.marker_rescale_steps,
        )
        if (
            match is None
            and self.marker_rescale_fallback
            and (
                self.marker_rescale_range != self.fallback_marker_rescale_range
                or self.marker_rescale_steps != self.fallback_marker_rescale_steps
            )
        ):
            match = self.find_best_marker_match(
                quad,
                self.fallback_marker_rescale_range,
                self.fallback_marker_rescale_steps,
            )
        return match

    def get_quadrant_matches(self, quads):
        matches = []
        for k in range(0, 4):
            match = self.get_quadrant_match(quads[k])
            if match is None:
                return None
            matches.append(match)
        return matches

    def find_best_marker_match(
        self,
        image_eroded_sub,
        marker_rescale_range=None,
        marker_rescale_steps=None,
        min_threshold=None,
    ):
        marker_rescale_range = marker_rescale_range or self.marker_rescale_range
        marker_rescale_steps = marker_rescale_steps or self.marker_rescale_steps
        min_threshold = (
            self.min_quadrant_matching_threshold
            if min_threshold is None
            else min_threshold
        )
        descent_per_step = (
            marker_rescale_range[1] - marker_rescale_range[0]
        ) // marker_rescale_steps
        descent_per_step = max(1, descent_per_step)
        _h, _w = self.marker.shape[:2]
        best = None

        for r0 in np.arange(
            marker_rescale_range[1],
            marker_rescale_range[0],
            -1 * descent_per_step,
        ):
            s = float(r0 * 1 / 100)
            if s == 0.0:
                continue
            rescaled_marker = ImageUtils.resize_util_h(
                self.marker, u_height=int(_h * s)
            )
            marker_h, marker_w = rescaled_marker.shape[:2]
            search_h, search_w = image_eroded_sub.shape[:2]
            if marker_h > search_h or marker_w > search_w:
                continue
            res = cv2.matchTemplate(
                image_eroded_sub, rescaled_marker, cv2.TM_CCOEFF_NORMED
            )
            max_t = res.max()
            if best is None or best["score"] < max_t:
                best = {
                    "scale": s,
                    "score": max_t,
                    "res": res,
                    "height": marker_h,
                    "width": marker_w,
                }

        if best is None or best["score"] < min_threshold:
            return None
        return best

    # Resizing the marker within scaleRange at rate of descent_per_step to
    # find the best match.
    def getBestMatch(self, image_eroded_sub, marker_rescale_range=None, marker_rescale_steps=None):
        config = self.tuning_config
        marker_rescale_range = marker_rescale_range or self.marker_rescale_range
        marker_rescale_steps = marker_rescale_steps or self.marker_rescale_steps
        descent_per_step = (
            marker_rescale_range[1] - marker_rescale_range[0]
        ) // marker_rescale_steps
        descent_per_step = max(1, descent_per_step)
        _h, _w = self.marker.shape[:2]
        res, best_scale = None, None
        all_max_t = 0

        for r0 in np.arange(
            marker_rescale_range[1],
            marker_rescale_range[0],
            -1 * descent_per_step,
        ):  # reverse order
            s = float(r0 * 1 / 100)
            if s == 0.0:
                continue
            rescaled_marker = ImageUtils.resize_util_h(
                self.marker, u_height=int(_h * s)
            )
            # res is the black image with white dots
            res = cv2.matchTemplate(
                image_eroded_sub, rescaled_marker, cv2.TM_CCOEFF_NORMED
            )

            max_t = res.max()
            if all_max_t < max_t:
                # print('Scale: '+str(s)+', Circle Match: '+str(round(max_t*100,2))+'%')
                best_scale, all_max_t = s, max_t

        if all_max_t < self.min_matching_threshold:
            logger.warning(
                "\tTemplate matching too low! Consider rechecking preProcessors applied before this."
            )
            if config.outputs.show_image_level >= 1:
                InteractionUtils.show("res", res, 1, 0, config=config)
            # Fail early: do not use this image (e.g. not an OMR sheet, or markers not in 4 corners)
            return None, all_max_t

        if best_scale is None:
            logger.warning(
                "No matchings for given scaleRange:", marker_rescale_range
            )
        return best_scale, all_max_t
