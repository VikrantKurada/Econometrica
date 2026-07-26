"""Downloading a run.

One route with a format, rather than a route per format: the thing being
exported is the same run either way, and a client that wants a different file
should not have to know a different URL shape. The format is an enum, so an
unknown one is a 422 naming the ones that exist rather than a 404 that looks
like the run is missing.

Chart images are not served from here. The renderers are TypeScript, so the
only faithful PNG is the one the browser is already displaying — the frontend
exports that itself, and this router owns the data and the prose.
"""

from enum import StrEnum
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from econometrica.api.deps import SessionDep
from econometrica.db.models import Run
from econometrica.services import exports

router = APIRouter(prefix="/api/runs", tags=["exports"])


class ExportFormat(StrEnum):
    json = "json"
    markdown = "markdown"
    csv = "csv"
    xlsx = "xlsx"
    zip = "zip"


_FORMATS = {
    ExportFormat.json: (exports.to_json, "application/json", ".json"),
    ExportFormat.markdown: (exports.to_markdown, "text/markdown; charset=utf-8", ".md"),
    ExportFormat.csv: (exports.to_csv, "text/csv; charset=utf-8", ".csv"),
    ExportFormat.xlsx: (
        exports.to_xlsx,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
    ExportFormat.zip: (exports.to_zip, "application/zip", ".zip"),
}


@router.get("/{run_id}/export")
async def export_run(run_id: UUID, session: SessionDep, format: ExportFormat) -> Response:
    """One run as a file, with the manifest that reproduces it.

    Built entirely from the stored outcome: no analysis is replayed and no
    model is asked anything, so exporting a run from last month costs one
    SELECT.
    """
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found"
        )

    render, content_type, suffix = _FORMATS[format]
    filename = exports.export_filename(run, suffix)

    return Response(
        content=render(run),
        media_type=content_type,
        # `attachment` rather than inline: these are files to keep, and a
        # browser rendering a CSV in a tab loses the name it was given.
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
