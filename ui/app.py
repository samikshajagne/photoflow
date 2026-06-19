"""
PhotoFlow Streamlit UI (Milestone 3).

A thin presentation layer over the existing pipeline. It does **no** image
analysis itself: it collects an input/output folder, calls
:meth:`core.pipeline.PhotoFlowPipeline.run`, and shows the resulting counts.
All detection, scoring, and organizing logic lives in ``core`` and is reused
unchanged.

The app is a three-step wizard driven by ``st.session_state``:

    1. Setup    -- choose input + output folders, start the run.
    2. Running  -- progress/status while the pipeline executes.
    3. Results  -- dashboard of BestShots/Duplicates/Blurry/Review counts,
                   with buttons to open the output and BestShots folders.

Run with:  streamlit run ui/app.py
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from core.organizer import (
    FOLDER_BEST_SHOTS,
    FOLDER_BLURRY,
    FOLDER_DUPLICATES,
    FOLDER_REVIEW,
)
from core.pipeline import PhotoFlowPipeline, PipelineError
from ui.components.folder_utils import (
    FolderError,
    reveal_folder,
    validate_input_folder,
    validate_output_folder,
)
from utils.config import ConfigError, load_config
from utils.logger import get_logger, setup_logging

logger = get_logger("ui.app")

STAGE_SETUP = "setup"
STAGE_RUNNING = "running"
STAGE_RESULTS = "results"


@st.cache_resource(show_spinner=False)
def _get_pipeline() -> PhotoFlowPipeline:
    """
    Build (once) the configured pipeline and wire up logging.

    Cached for the app's lifetime so the heavyweight components (e.g. the
    face detector) are constructed a single time and reused across runs.
    """
    config = load_config()
    setup_logging(config.logging)
    logger.info("Streamlit UI initialized; pipeline constructed from config.")
    return PhotoFlowPipeline.from_config(config)


def _init_state() -> None:
    ss = st.session_state
    ss.setdefault("stage", STAGE_SETUP)
    ss.setdefault("input_folder", "")
    ss.setdefault("output_folder", "")
    ss.setdefault("result", None)
    ss.setdefault("error", None)


def _step_caption() -> None:
    labels = {STAGE_SETUP: "1. Setup", STAGE_RUNNING: "2. Analyzing", STAGE_RESULTS: "3. Results"}
    st.caption(" › ".join(
        f"**{v}**" if k == st.session_state.stage else v for k, v in labels.items()
    ))


# --------------------------------------------------------------------------- #
# Step 1: setup
# --------------------------------------------------------------------------- #
def _render_setup() -> None:
    st.subheader("Select folders")
    st.write(
        "Choose the folder of photos to analyze and where to write the "
        "organized results. Your original files are never modified."
    )

    input_folder = st.text_input(
        "Input folder (your photos)",
        value=st.session_state.input_folder,
        placeholder=r"e.g. C:\Users\you\Pictures\Trip",
        key="input_folder_field",
    )
    output_folder = st.text_input(
        "Output folder (where PhotoFlow_Output is created)",
        value=st.session_state.output_folder,
        placeholder=r"e.g. C:\Users\you\Pictures\Trip\sorted",
        key="output_folder_field",
    )

    if st.button("Start analysis", type="primary"):
        ok_in, msg_in = validate_input_folder(input_folder)
        ok_out, msg_out = validate_output_folder(output_folder)
        if not ok_in:
            st.error(msg_in)
        elif not ok_out:
            st.error(msg_out)
        else:
            st.session_state.input_folder = input_folder.strip()
            st.session_state.output_folder = output_folder.strip()
            st.session_state.result = None
            st.session_state.error = None
            st.session_state.stage = STAGE_RUNNING
            logger.info(
                "UI run requested: input='%s' output='%s'",
                st.session_state.input_folder,
                st.session_state.output_folder,
            )
            st.rerun()


# --------------------------------------------------------------------------- #
# Step 2: running
# --------------------------------------------------------------------------- #
def _render_running() -> None:
    st.subheader("Analyzing your photos")
    with st.status("Running the PhotoFlow pipeline...", expanded=True) as status:
        st.write("Scanning folder and detecting duplicates...")
        st.write("Measuring blur, detecting faces, scoring quality...")
        st.write("Organizing copies into BestShots / Duplicates / Blurry / Review...")
        try:
            pipeline = _get_pipeline()
            result = pipeline.run(
                input_folder=st.session_state.input_folder,
                destination_root=st.session_state.output_folder,
                dry_run=False,
            )
            st.session_state.result = result
            status.update(label="Analysis complete", state="complete")
            logger.info("UI run complete: %s", result.category_counts)
        except (PipelineError, ConfigError) as exc:
            st.session_state.error = str(exc)
            status.update(label="Analysis failed", state="error")
            logger.error("UI run failed: %s", exc)

    st.session_state.stage = STAGE_RESULTS
    st.rerun()


# --------------------------------------------------------------------------- #
# Step 3: results
# --------------------------------------------------------------------------- #
def _render_results() -> None:
    st.subheader("Results")

    if st.session_state.error:
        st.error(f"Analysis failed: {st.session_state.error}")
        if st.button("Back to start"):
            st.session_state.stage = STAGE_SETUP
            st.rerun()
        return

    result = st.session_state.result
    if result is None:  # pragma: no cover - defensive
        st.session_state.stage = STAGE_SETUP
        st.rerun()
        return

    counts = result.category_counts
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Best shots", counts.get(FOLDER_BEST_SHOTS, 0))
    col2.metric("Duplicates", counts.get(FOLDER_DUPLICATES, 0))
    col3.metric("Blurry", counts.get(FOLDER_BLURRY, 0))
    col4.metric("Review", counts.get(FOLDER_REVIEW, 0))

    st.caption(
        f"Scanned {result.scanned_count} image(s) - "
        f"{result.duplicate_group_count} duplicate group(s), "
        f"{result.faces_detected_count} with faces."
    )

    output_root = result.output_root
    st.write(f"Output written to: `{output_root}`")

    col_open, col_best = st.columns(2)
    if col_open.button("Open Output Folder"):
        _try_reveal(output_root)
    if col_best.button("Open BestShots Folder"):
        _try_reveal(str(Path(output_root) / FOLDER_BEST_SHOTS))

    st.divider()
    if st.button("Analyze another folder"):
        st.session_state.stage = STAGE_SETUP
        st.session_state.result = None
        st.session_state.error = None
        st.rerun()


def _try_reveal(path: str) -> None:
    try:
        reveal_folder(path)
        st.toast(f"Opened {path}")
    except FolderError as exc:
        st.error(str(exc))


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main() -> None:
    st.set_page_config(page_title="PhotoFlow", page_icon="📸", layout="centered")
    _init_state()

    st.title("PhotoFlow")
    _step_caption()
    st.divider()

    stage = st.session_state.stage
    if stage == STAGE_RUNNING:
        _render_running()
    elif stage == STAGE_RESULTS:
        _render_results()
    else:
        _render_setup()


if __name__ == "__main__":
    main()
