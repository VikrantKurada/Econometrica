"""Shared plumbing for the multivariate family.

Multivariate tools operate on a system of columns rather than a single
series, so the family has its own frame-preparation helper (mirroring the
role ``prepare_series`` plays for the univariate families).
"""

import pandas as pd

from econometrica.econ._common import require_columns

COLUMNS_FIELD_DOC = (
    "Columns to include as system variables, in order; an empty list means"
    " every column of the supplied frame. Order matters for tools that"
    " orthogonalize by Cholesky (irf, fevd)."
)


def prepare_frame(
    data: pd.DataFrame,
    columns: list[str],
    *,
    min_obs: int,
    tool: str,
    min_columns: int = 2,
) -> pd.DataFrame:
    """Resolve the column selection and validate the multivariate frame.

    An empty ``columns`` selects every column in frame order. Rows with a NaN
    in any selected column are dropped — the estimators need a balanced
    system. Raises on unknown columns, on fewer than ``min_columns`` selected
    and on fewer than ``min_obs`` complete rows.
    """
    selected = columns or [str(c) for c in data.columns]
    require_columns(data, selected, tool=tool)
    if len(selected) < min_columns:
        raise ValueError(
            f"{tool}: needs at least {min_columns} columns, got {len(selected)}"
            f" ({selected}); select the system columns via the 'columns' param"
        )
    frame = data[selected].dropna().astype(float)
    if len(frame) < min_obs:
        raise ValueError(
            f"{tool}: needs at least {min_obs} complete observations, got"
            f" {len(frame)}; supply more data or lower min_obs"
        )
    return frame
