"""The multi-agent layer.

Six roles sit between a question in prose and an answer with numbers in it.
None of them computes a statistic: they select from the tool registry, and two
deterministic gates — the diagnostics engine and the numeric grounding
check — stand between what a model says and what a user sees.
"""
