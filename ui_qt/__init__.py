"""
PhotoFlow PyQt6 desktop UI.

A native desktop front-end over the existing PhotoFlow pipeline. Like the
Streamlit ``ui`` package, this layer contains no image-processing logic: it
reuses ``core.pipeline.PhotoFlowPipeline`` unchanged and only presents its
results. The two front-ends are interchangeable.

Phase 1 delivers the application shell (window, dark theme, toolbar,
three-panel layout) and browse-before-analyze folder loading. Analysis (run
in a separate process) and lazy thumbnail loading arrive in later phases.
"""
