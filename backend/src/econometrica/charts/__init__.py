"""Declarative chart specifications.

The Visualizer emits these as JSON from a closed vocabulary and never writes
drawing code — the same containment the tool registry gives the econometrics.
A renderer is a switch over these types; a spec that does not validate is
rejected and retried, exactly as a malformed plan is.
"""
