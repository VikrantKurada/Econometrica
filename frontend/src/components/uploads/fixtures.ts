import type { Upload } from "../../lib/types";

/**
 * A realistic upload for the dev gallery — the shape a Yahoo CSV export
 * actually profiles to, including the ambiguous `Volume` column, so the
 * "unsure" marker and the reasons can be looked at rather than only asserted.
 *
 * Not part of the app; `vite build` takes only index.html.
 */
export const UPLOAD_FIXTURE: Upload = {
  id: "00000000-0000-0000-0000-000000000000",
  project_id: "00000000-0000-0000-0000-000000000001",
  filename: "AAPL.csv",
  consulted_model: true,
  confirmed: false,
  mapping: null,
  observations: null,
  symbols: [],
  fields: [],
  profile: {
    filename: "AAPL.csv",
    format: "csv",
    rows: 123,
    layout: "wide",
    delimiter: ",",
    columns: [
      {
        name: "Date",
        dtype: "datetime",
        present: 123,
        missing: 0,
        unique: 123,
        minimum: null,
        maximum: null,
        sample: ["2023-01-03"],
        parses_as_date: true,
        decimal_comma: false,
        candidates: [{ role: "date", score: 1, reason: "values parse as dates" }],
      },
      {
        name: "Adj Close",
        dtype: "number",
        present: 123,
        missing: 0,
        unique: 121,
        minimum: 122.93,
        maximum: 186.97,
        sample: ["122.93"],
        parses_as_date: false,
        decimal_comma: false,
        candidates: [
          { role: "price", score: 1, reason: "strictly positive values from 122.93 to 186.97" },
        ],
      },
      {
        name: "Close",
        dtype: "number",
        present: 123,
        missing: 0,
        unique: 121,
        minimum: 125.02,
        maximum: 189.59,
        sample: ["125.07"],
        parses_as_date: false,
        decimal_comma: false,
        candidates: [
          { role: "price", score: 1, reason: "strictly positive values from 125.02 to 189.59" },
        ],
      },
      {
        name: "Volume",
        dtype: "number",
        present: 123,
        missing: 2,
        unique: 123,
        minimum: 37266700,
        maximum: 154357300,
        sample: ["112117500"],
        parses_as_date: false,
        decimal_comma: false,
        candidates: [
          {
            role: "volume",
            score: 1,
            reason: "whole non-negative values of a size typical of volumes",
          },
          {
            role: "price",
            score: 0.6,
            reason: "strictly positive values from 3.7267e+07 to 1.54357e+08",
          },
        ],
      },
    ],
  },
  proposal: {
    roles: {
      Date: "date",
      "Adj Close": "price",
      Close: "price",
      Volume: "volume",
    },
    rationale: {
      Date: "values parse as dates",
      "Adj Close": "strictly positive values from 122.93 to 186.97",
      Close: "strictly positive values from 125.02 to 189.59",
      Volume: "Values are whole numbers typical of trading volumes, not prices",
    },
    ambiguous: ["Volume"],
  },
};
