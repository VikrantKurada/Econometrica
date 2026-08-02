import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { installFakeApi, makeDataset, makeProject, type FakeApi } from "../../test/fakeApi";
import { renderWithProviders } from "../../test/renderWithProviders";
import { ProjectData } from "./ProjectData";

let fake: FakeApi;

beforeEach(() => {
  vi.unstubAllGlobals();
  fake = installFakeApi();
});

const csv = () => new File(["date,AAA\n2020-01-01,100\n"], "prices.csv", { type: "text/csv" });

describe("ProjectData", () => {
  it("lists a project's datasets", async () => {
    const project = makeProject();
    fake.projects.push(project);
    fake.datasets.push(makeDataset(project.id, { name: "prices.csv" }));

    renderWithProviders(<ProjectData projectId={project.id} />);

    expect(await screen.findByText("prices.csv")).toBeInTheDocument();
  });

  it("shows the mapping screen after a file is chosen, then returns to the list", async () => {
    const project = makeProject();
    fake.projects.push(project);
    const { user } = renderWithProviders(<ProjectData projectId={project.id} />);

    await screen.findByText(/No data yet/);
    await user.upload(screen.getByLabelText("Upload file"), csv());

    // The mapping table appears (its confirm button is unique to it).
    expect(await screen.findByRole("button", { name: /confirm mapping/i })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /confirm mapping/i }));

    // Back to the list, now showing the ingested dataset.
    await waitFor(() => expect(screen.getByLabelText("Datasets")).toBeInTheDocument());
    expect(fake.callsTo("POST", "/confirm")).toHaveLength(1);
  });

  it("surfaces an upload error and stays on the list", async () => {
    const project = makeProject();
    fake.projects.push(project);
    // Make the uploads route reject, like a 415 unsupported type.
    fake.fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const path = typeof input === "string" ? input : input.toString();
      if (path.endsWith("/uploads")) {
        return new Response(JSON.stringify({ detail: "cannot index .xlsx" }), { status: 415 });
      }
      return new Response(JSON.stringify([]), { status: 200 });
    });
    const { user } = renderWithProviders(<ProjectData projectId={project.id} />);

    await screen.findByText(/No data yet/);
    await user.upload(screen.getByLabelText("Upload file"), csv());

    expect(await screen.findByRole("alert")).toHaveTextContent("cannot index");
    expect(screen.queryByRole("button", { name: /confirm mapping/i })).not.toBeInTheDocument();
  });
});
