"""

 OMRChecker

 Author: Udayraj Deshmukh
 Github: https://github.com/Udayraj123

"""
import os
from csv import QUOTE_NONNUMERIC
from pathlib import Path
from time import perf_counter, time

import cv2
import pandas as pd
from rich.table import Table

from src.constants.common import (
    CONFIG_FILENAME,
    ERROR_CODES,
    EVALUATION_FILENAME,
    TEMPLATE_FILENAME,
)
from src.defaults import CONFIG_DEFAULTS
from src.evaluation import EvaluationConfig, evaluate_concatenated_response
from src.logger import console, logger
from src.template import Template
from src.utils.file import Paths, setup_dirs_for_paths, setup_outputs_for_template
from src.utils.image import ImageUtils
from src.utils.interaction import InteractionUtils, Stats
from src.utils.parsing import get_concatenated_response, open_config_with_defaults

# Load processors
STATS = Stats()


def entry_point(input_dir, args):
    if not os.path.exists(input_dir):
        raise Exception(f"Given input directory does not exist: '{input_dir}'")
    input_dir = Path(input_dir)
    curr_dir = input_dir
    started_at = perf_counter()
    if args.get("single_file"):
        result = process_single_file(input_dir, Path(args["single_file"]), args)
    else:
        result = process_dir(input_dir, curr_dir, args)
    print(
        "[OMR entry_point timing] "
        f"total_ms={round((perf_counter() - started_at) * 1000, 2)}",
        flush=True,
    )
    return result


def process_single_file(root_dir, file_path, args):
    timing_started_at = perf_counter()
    timing_stage_started_at = timing_started_at
    timings_ms: dict[str, float] = {}

    def mark_timing(name: str) -> None:
        nonlocal timing_stage_started_at
        now = perf_counter()
        timings_ms[name] = round((now - timing_stage_started_at) * 1000, 2)
        timing_stage_started_at = now

    root_dir = Path(root_dir)
    file_path = Path(file_path)
    if not file_path.is_file():
        raise Exception(f"Given input file does not exist: '{file_path}'")

    local_config_path = root_dir.joinpath(CONFIG_FILENAME)
    tuning_config = (
        open_config_with_defaults(local_config_path)
        if os.path.exists(local_config_path)
        else CONFIG_DEFAULTS
    )
    mark_timing("open_config")

    local_template_path = root_dir.joinpath(TEMPLATE_FILENAME)
    if not os.path.exists(local_template_path):
        raise Exception(f"No template file found in the directory tree of {root_dir}")
    template = Template(local_template_path, tuning_config)
    mark_timing("template")

    excluded_files = []
    for pp in template.pre_processors:
        excluded_files.extend(Path(p) for p in pp.exclude_files())
    if file_path in excluded_files:
        raise Exception(f"Input file is excluded by template pre-processors: '{file_path}'")
    mark_timing("discover_files")

    evaluation_config = None
    local_evaluation_path = root_dir.joinpath(EVALUATION_FILENAME)
    if not args["setLayout"] and os.path.exists(local_evaluation_path):
        evaluation_config = EvaluationConfig(
            root_dir,
            local_evaluation_path,
            template,
            tuning_config,
        )
        excluded_files.extend(
            Path(exclude_file) for exclude_file in evaluation_config.get_exclude_files()
        )
        if file_path in excluded_files:
            raise Exception(f"Input file is excluded by evaluation config: '{file_path}'")
    mark_timing("evaluation_config")

    output_dir = Path(args["output_dir"], file_path.parent.relative_to(root_dir))
    paths = Paths(output_dir)
    setup_dirs_for_paths(paths)
    return_result = bool(args.get("return_result"))
    outputs_namespace = setup_outputs_for_template(
        paths,
        template,
        write_csv=not return_result,
    )
    mark_timing("setup_outputs")

    processing_results = process_files(
        [file_path],
        template,
        tuning_config,
        evaluation_config,
        outputs_namespace,
        collect_results=return_result,
    )
    mark_timing("process_files")

    timings_ms["total"] = round((perf_counter() - timing_started_at) * 1000, 2)
    print(
        "[OMR single_file timing] "
        f"file={file_path.name} "
        f"open_config_ms={timings_ms.get('open_config', 0)} "
        f"template_ms={timings_ms.get('template', 0)} "
        f"discover_files_ms={timings_ms.get('discover_files', 0)} "
        f"evaluation_config_ms={timings_ms.get('evaluation_config', 0)} "
        f"setup_outputs_ms={timings_ms.get('setup_outputs', 0)} "
        f"process_files_ms={timings_ms.get('process_files', 0)} "
        f"total_ms={timings_ms.get('total')}",
        flush=True,
    )
    if return_result and processing_results:
        return processing_results[0]
    return None


def print_config_summary(
    curr_dir,
    omr_files,
    template,
    tuning_config,
    local_config_path,
    evaluation_config,
    args,
):
    logger.info("")
    table = Table(title="Current Configurations", show_header=False, show_lines=False)
    table.add_column("Key", style="cyan", no_wrap=True)
    table.add_column("Value", style="magenta")
    table.add_row("Directory Path", f"{curr_dir}")
    table.add_row("Count of Images", f"{len(omr_files)}")
    table.add_row("Set Layout Mode ", "ON" if args["setLayout"] else "OFF")
    pre_processor_names = [pp.__class__.__name__ for pp in template.pre_processors]
    table.add_row(
        "Markers Detection",
        "ON" if "CropOnMarkers" in pre_processor_names else "OFF",
    )
    table.add_row("Auto Alignment", f"{tuning_config.alignment_params.auto_align}")
    table.add_row("Detected Template Path", f"{template}")
    if local_config_path:
        table.add_row("Detected Local Config", f"{local_config_path}")
    if evaluation_config:
        table.add_row("Detected Evaluation Config", f"{evaluation_config}")

    table.add_row(
        "Detected pre-processors",
        ", ".join(pre_processor_names),
    )
    console.print(table, justify="center")


def process_dir(
    root_dir,
    curr_dir,
    args,
    template=None,
    tuning_config=CONFIG_DEFAULTS,
    evaluation_config=None,
):
    timing_started_at = perf_counter()
    timing_stage_started_at = timing_started_at
    timings_ms: dict[str, float] = {}

    def mark_timing(name: str) -> None:
        nonlocal timing_stage_started_at
        now = perf_counter()
        timings_ms[name] = round((now - timing_stage_started_at) * 1000, 2)
        timing_stage_started_at = now

    # Update local tuning_config (in current recursion stack)
    local_config_path = curr_dir.joinpath(CONFIG_FILENAME)
    if os.path.exists(local_config_path):
        tuning_config = open_config_with_defaults(local_config_path)
    mark_timing("open_config")

    # Update local template (in current recursion stack)
    local_template_path = curr_dir.joinpath(TEMPLATE_FILENAME)
    local_template_exists = os.path.exists(local_template_path)
    if local_template_exists:
        template = Template(
            local_template_path,
            tuning_config,
        )
    mark_timing("template")
    # Look for subdirectories for processing
    subdirs = [d for d in curr_dir.iterdir() if d.is_dir()]

    output_dir = Path(args["output_dir"], curr_dir.relative_to(root_dir))
    paths = Paths(output_dir)

    # look for images in current dir to process
    exts = ("*.[pP][nN][gG]", "*.[jJ][pP][gG]", "*.[jJ][pP][eE][gG]")
    omr_files = sorted([f for ext in exts for f in curr_dir.glob(ext)])

    # Exclude images (take union over all pre_processors)
    excluded_files = []
    if template:
        for pp in template.pre_processors:
            excluded_files.extend(Path(p) for p in pp.exclude_files())
    mark_timing("discover_files")

    local_evaluation_path = curr_dir.joinpath(EVALUATION_FILENAME)
    if not args["setLayout"] and os.path.exists(local_evaluation_path):
        if not local_template_exists:
            logger.warning(
                f"Found an evaluation file without a parent template file: {local_evaluation_path}"
            )
        evaluation_config = EvaluationConfig(
            curr_dir,
            local_evaluation_path,
            template,
            tuning_config,
        )

        excluded_files.extend(
            Path(exclude_file) for exclude_file in evaluation_config.get_exclude_files()
        )
    mark_timing("evaluation_config")

    omr_files = [f for f in omr_files if f not in excluded_files]

    if omr_files:
        if not template:
            logger.error(
                f"Found images, but no template in the directory tree \
                of '{curr_dir}'. \nPlace {TEMPLATE_FILENAME} in the \
                appropriate directory."
            )
            raise Exception(
                f"No template file found in the directory tree of {curr_dir}"
            )

        setup_dirs_for_paths(paths)
        outputs_namespace = setup_outputs_for_template(paths, template)
        mark_timing("setup_outputs")

        if not args.get("skip_config_table"):
            print_config_summary(
                curr_dir,
                omr_files,
                template,
                tuning_config,
                local_config_path,
                evaluation_config,
                args,
            )
        if args["setLayout"]:
            show_template_layouts(omr_files, template, tuning_config)
        else:
            process_files(
                omr_files,
                template,
                tuning_config,
                evaluation_config,
                outputs_namespace,
            )
        mark_timing("process_files")

    elif not subdirs:
        # Each subdirectory should have images or should be non-leaf
        logger.info(
            f"No valid images or sub-folders found in {curr_dir}.\
            Empty directories not allowed."
        )

    # recursively process sub-folders
    for d in subdirs:
        process_dir(
            root_dir,
            d,
            args,
            template,
            tuning_config,
            evaluation_config,
        )

    timings_ms["total"] = round((perf_counter() - timing_started_at) * 1000, 2)
    print(
        "[OMR process_dir timing] "
        f"dir={curr_dir.name} "
        f"open_config_ms={timings_ms.get('open_config', 0)} "
        f"template_ms={timings_ms.get('template', 0)} "
        f"discover_files_ms={timings_ms.get('discover_files', 0)} "
        f"evaluation_config_ms={timings_ms.get('evaluation_config', 0)} "
        f"setup_outputs_ms={timings_ms.get('setup_outputs', 0)} "
        f"process_files_ms={timings_ms.get('process_files', 0)} "
        f"total_ms={timings_ms.get('total')}",
        flush=True,
    )


def show_template_layouts(omr_files, template, tuning_config):
    for file_path in omr_files:
        file_name = file_path.name
        file_path = str(file_path)
        in_omr = cv2.imread(file_path, cv2.IMREAD_GRAYSCALE)
        in_omr = template.image_instance_ops.apply_preprocessors(
            file_path, in_omr, template
        )
        template_layout = template.image_instance_ops.draw_template_layout(
            in_omr, template, shifted=False, border=2
        )
        InteractionUtils.show(
            f"Template Layout: {file_name}", template_layout, 1, 1, config=tuning_config
        )


def process_files(
    omr_files,
    template,
    tuning_config,
    evaluation_config,
    outputs_namespace,
    collect_results=False,
):
    start_time = int(time())
    timing_started_at = perf_counter()
    timing_stage_started_at = timing_started_at
    timings_ms: dict[str, float] = {}

    def mark_timing(name: str) -> None:
        nonlocal timing_stage_started_at
        now = perf_counter()
        elapsed_ms = round((now - timing_stage_started_at) * 1000, 2)
        timings_ms[name] = round(timings_ms.get(name, 0) + elapsed_ms, 2)
        timing_stage_started_at = now

    files_counter = 0
    processing_results = []
    STATS.files_not_moved = 0

    for file_path in omr_files:
        files_counter += 1
        file_name = file_path.name

        in_omr = cv2.imread(str(file_path), cv2.IMREAD_GRAYSCALE)
        mark_timing("read_image")
        if in_omr is None:
            raise ValueError(f"Unable to read image file: '{file_path}'")

        logger.info("")
        logger.info(
            f"({files_counter}) Opening image: \t'{file_path}'\tResolution: {in_omr.shape}"
        )

        template.image_instance_ops.reset_all_save_img()

        template.image_instance_ops.append_save_img(1, in_omr)

        in_omr = template.image_instance_ops.apply_preprocessors(
            file_path, in_omr, template
        )
        mark_timing("preprocess")

        if in_omr is None:
            # Error OMR case
            new_file_path = outputs_namespace.paths.errors_dir.joinpath(file_name)
            outputs_namespace.OUTPUT_SET.append(
                [file_name] + outputs_namespace.empty_resp
            )
            if collect_results:
                processing_results.append(
                    {
                        "error_code": "markers_not_found",
                        "file_id": file_name,
                    }
                )
            elif check_and_move(
                ERROR_CODES.NO_MARKER_ERR,
                file_path,
                new_file_path,
            ):
                err_line = [
                    file_name,
                    file_path,
                    new_file_path,
                    "NA",
                ] + outputs_namespace.empty_resp
                pd.DataFrame(err_line, dtype=str).T.to_csv(
                    outputs_namespace.files_obj["Errors"],
                    mode="a",
                    quoting=QUOTE_NONNUMERIC,
                    header=False,
                    index=False,
                )
            mark_timing("write_error")
            continue

        # uniquify
        file_id = str(file_name)
        save_dir = outputs_namespace.paths.save_marked_dir
        (
            response_dict,
            final_marked,
            multi_marked,
            _,
        ) = template.image_instance_ops.read_omr_response(
            template,
            image=in_omr,
            name=file_id,
            save_dir=save_dir,
            evaluation_config=evaluation_config,
        )
        mark_timing("read_response")

        grid_alignment_failures = list(
            template.image_instance_ops.last_grid_alignment_failures
        )
        if grid_alignment_failures:
            logger.warning(
                "[OMR bubble grid] rejecting file=%s failed_blocks=%s",
                file_id,
                ",".join(grid_alignment_failures),
            )
            if collect_results:
                processing_results.append(
                    {
                        "error_code": "bubble_grid_not_found",
                        "file_id": file_name,
                        "field_blocks": grid_alignment_failures,
                    }
                )
            mark_timing("write_error")
            continue

        # TODO: move inner try catch here
        # concatenate roll nos, set unmarked responses, etc
        omr_response = get_concatenated_response(response_dict, template)
        mark_timing("concatenate_response")

        if (
            evaluation_config is None
            or not evaluation_config.get_should_explain_scoring()
        ):
            logger.info(f"Read Response: {file_id} ({len(omr_response)} keys)")
            logger.debug("Read Response full: %s", omr_response)

        score = 0
        if evaluation_config is not None:
            score = evaluate_concatenated_response(
                omr_response,
                evaluation_config,
                file_path,
                outputs_namespace.paths.evaluation_dir,
            )
            mark_timing("evaluate")
            logger.info(
                f"(/{files_counter}) Graded with score: {round(score, 2)}\t for file: '{file_id}'"
            )
        else:
            mark_timing("evaluate")
            logger.info(f"(/{files_counter}) Processed file: '{file_id}'")

        if tuning_config.outputs.show_image_level >= 2:
            InteractionUtils.show(
                f"Final Marked Bubbles : '{file_id}'",
                ImageUtils.resize_util_h(
                    final_marked, int(tuning_config.dimensions.display_height * 1.3)
                ),
                1,
                1,
                config=tuning_config,
            )

        resp_array = []
        for k in template.output_columns:
            resp_array.append(omr_response[k])

        outputs_namespace.OUTPUT_SET.append([file_name] + resp_array)

        if multi_marked == 0 or not tuning_config.outputs.filter_out_multimarked_files:
            STATS.files_not_moved += 1
            new_file_path = save_dir.joinpath(file_id)
            if collect_results:
                processing_results.append(
                    {
                        "file_id": file_name,
                        "input_path": str(file_path),
                        "output_path": str(new_file_path),
                        "score": float(score),
                        "responses": {
                            key: omr_response[key] for key in template.output_columns
                        },
                    }
                )
            else:
                # Enter into Results sheet-
                results_line = [file_name, file_path, new_file_path, score] + resp_array
                # Write/Append to results_line file(opened in append mode)
                pd.DataFrame(results_line, dtype=str).T.to_csv(
                    outputs_namespace.files_obj["Results"],
                    mode="a",
                    quoting=QUOTE_NONNUMERIC,
                    header=False,
                    index=False,
                )
            mark_timing("write_results")
        else:
            # multi_marked file
            logger.info(f"[{files_counter}] Found multi-marked file: '{file_id}'")
            new_file_path = outputs_namespace.paths.multi_marked_dir.joinpath(file_name)
            if collect_results:
                processing_results.append(
                    {
                        "error_code": "multiple_marks",
                        "file_id": file_name,
                    }
                )
            elif check_and_move(
                ERROR_CODES.MULTI_BUBBLE_WARN,
                file_path,
                new_file_path,
            ):
                mm_line = [file_name, file_path, new_file_path, "NA"] + resp_array
                pd.DataFrame(mm_line, dtype=str).T.to_csv(
                    outputs_namespace.files_obj["MultiMarked"],
                    mode="a",
                    quoting=QUOTE_NONNUMERIC,
                    header=False,
                    index=False,
                )
            mark_timing("write_results")
            # else:
            #     TODO:  Add appropriate record handling here
            #     pass

    timings_ms["total"] = round((perf_counter() - timing_started_at) * 1000, 2)
    print(
        "[OMR entry timing] "
        f"files={files_counter} "
        f"read_image_ms={timings_ms.get('read_image', 0)} "
        f"preprocess_ms={timings_ms.get('preprocess', 0)} "
        f"read_response_ms={timings_ms.get('read_response', 0)} "
        f"concatenate_response_ms={timings_ms.get('concatenate_response', 0)} "
        f"evaluate_ms={timings_ms.get('evaluate', 0)} "
        f"write_results_ms={timings_ms.get('write_results', 0)} "
        f"write_error_ms={timings_ms.get('write_error', 0)} "
        f"total_ms={timings_ms.get('total')}",
        flush=True,
    )

    print_stats(start_time, files_counter, tuning_config)
    return processing_results


def check_and_move(error_code, file_path, filepath2):
    # TODO: fix file movement into error/multimarked/invalid etc again
    STATS.files_not_moved += 1
    return True


def print_stats(start_time, files_counter, tuning_config):
    time_checking = max(1, round(time() - start_time, 2))
    log = logger.info
    log("")
    log(f"{'Total file(s) moved': <27}: {STATS.files_moved}")
    log(f"{'Total file(s) not moved': <27}: {STATS.files_not_moved}")
    log("--------------------------------")
    log(
        f"{'Total file(s) processed': <27}: {files_counter} ({'Sum Tallied!' if files_counter == (STATS.files_moved + STATS.files_not_moved) else 'Not Tallying!'})"
    )

    if tuning_config.outputs.show_image_level <= 0:
        log(
            f"\nFinished Checking {files_counter} file(s) in {round(time_checking, 1)} seconds i.e. ~{round(time_checking / 60, 1)} minute(s)."
        )
        log(
            f"{'OMR Processing Rate': <27}: \t ~ {round(time_checking / files_counter, 2)} seconds/OMR"
        )
        log(
            f"{'OMR Processing Speed': <27}: \t ~ {round((files_counter * 60) / time_checking, 2)} OMRs/minute"
        )
    else:
        log(f"\n{'Total script time': <27}: {time_checking} seconds")

    if tuning_config.outputs.show_image_level <= 1:
        log(
            "\nTip: To see some awesome visuals, open config.json and increase 'show_image_level'"
        )
