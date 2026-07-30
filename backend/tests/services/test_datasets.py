"""The label an ingested file carries into `DataQualityReport.source`."""

from datetime import UTC, datetime

import pytest

from econometrica.services.datasets import source_label
from econometrica.services.mapping import MappingError


def test_a_label_names_the_file_and_when_it_was_ingested():
    label = source_label("prices.csv", datetime(2024, 1, 5, tzinfo=UTC))
    assert label == "upload: prices.csv (ingested 2024-01-05)"


def test_a_filename_containing_synthetic_is_refused():
    """`DataQualityReport.source` flags generated data by substring.

    Once an upload's label is composed into that string, a file named this way
    would make a run on real data announce that its prices were generated. The
    filename is the one input a user controls, so it is the one to refuse.
    """
    with pytest.raises(MappingError) as exc:
        source_label("synthetic-test.csv", datetime(2024, 1, 5, tzinfo=UTC))

    assert "synthetic" in str(exc.value)
    assert "rename" in str(exc.value).lower()


def test_the_check_is_case_insensitive():
    with pytest.raises(MappingError):
        source_label("Synthetic_Prices.CSV", datetime(2024, 1, 5, tzinfo=UTC))
