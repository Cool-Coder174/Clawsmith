"""CLI-first phase execution engine.

Replaces IDE/GUI-based workflows with direct CLI agent invocation.
Each YOLO phase is executed by building a prompt, setting it into
``CLAWSMITH_PROMPT``, and running ``agent chat "$env:CLAWSMITH_PROMPT"``.
"""
