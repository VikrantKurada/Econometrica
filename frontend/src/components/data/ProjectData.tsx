import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ChangeEvent, useRef, useState } from "react";

import { api } from "../../lib/api";
import { queryKeys } from "../../lib/queryClient";
import type { ColumnRole, Upload } from "../../lib/types";
import { Button } from "../ui/Button";
import { ColumnMapping } from "../uploads/ColumnMapping";
import { DatasetList } from "./DatasetList";

/**
 * A project's Data view: its stored datasets, and the upload → map → confirm
 * flow. Rendered in the canvas slot when a project (but no chat) is selected.
 *
 * One piece of local state carries the whole flow: `upload` is `null` in list
 * mode and an `Upload` while its columns are being mapped. Nothing is stored
 * until Confirm — the human-in-the-loop the backend also enforces.
 */
export function ProjectData({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [upload, setUpload] = useState<Upload | null>(null);

  const datasets = useQuery({
    queryKey: queryKeys.datasets(projectId),
    queryFn: () => api.listDatasets(projectId),
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => api.uploadFile(projectId, file),
    onSuccess: (result) => setUpload(result),
  });

  const confirmMutation = useMutation({
    mutationFn: (vars: { uploadId: string; roles: Record<string, ColumnRole> }) =>
      api.confirmUpload(vars.uploadId, vars.roles),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.datasets(projectId) });
      setUpload(null);
    },
  });

  function pick(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    // Reset so choosing the same file again after a cancel still fires change.
    event.target.value = "";
    if (file) uploadMutation.mutate(file);
  }

  if (upload) {
    return (
      <div className="flex flex-col items-start gap-4 p-4">
        <ColumnMapping
          upload={upload}
          busy={confirmMutation.isPending}
          error={confirmMutation.isError ? (confirmMutation.error as Error).message : null}
          onConfirm={(roles) => confirmMutation.mutate({ uploadId: upload.id, roles })}
        />
        <Button
          variant="ghost"
          onClick={() => setUpload(null)}
          disabled={confirmMutation.isPending}
        >
          Cancel
        </Button>
      </div>
    );
  }

  return (
    <section aria-label="Project data" className="flex flex-col gap-4 p-4">
      <header className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-medium text-text-primary">Data</h2>
        <Button
          variant="primary"
          onClick={() => fileInput.current?.click()}
          disabled={uploadMutation.isPending}
        >
          {uploadMutation.isPending ? "Uploading…" : "Upload data"}
        </Button>
        <input
          ref={fileInput}
          type="file"
          aria-label="Upload file"
          accept=".csv,.tsv,.txt,.xlsx"
          className="sr-only"
          onChange={pick}
        />
      </header>

      {uploadMutation.isError && (
        <p role="alert" className="text-2xs text-negative">
          {(uploadMutation.error as Error).message}
        </p>
      )}

      {datasets.isPending ? (
        <p className="text-2xs text-text-secondary">Loading…</p>
      ) : (
        <DatasetList datasets={datasets.data ?? []} />
      )}
    </section>
  );
}
