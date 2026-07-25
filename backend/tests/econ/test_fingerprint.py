import numpy as np
import pandas as pd

from econometrica.econ.fingerprint import fingerprint_frame, fingerprint_params


def test_identical_frames_produce_identical_fingerprints():
    df = pd.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]})
    assert fingerprint_frame(df) == fingerprint_frame(df.copy())


def test_different_values_produce_different_fingerprints():
    a = pd.DataFrame({"x": [1.0, 2.0]})
    b = pd.DataFrame({"x": [1.0, 2.000001]})
    assert fingerprint_frame(a) != fingerprint_frame(b)


def test_column_order_affects_fingerprint():
    a = pd.DataFrame({"x": [1.0], "y": [2.0]})
    b = a[["y", "x"]]
    assert fingerprint_frame(a) != fingerprint_frame(b)


def test_index_is_part_of_the_fingerprint():
    a = pd.DataFrame({"x": [1.0]}, index=pd.to_datetime(["2020-01-01"]))
    b = pd.DataFrame({"x": [1.0]}, index=pd.to_datetime(["2020-01-02"]))
    assert fingerprint_frame(a) != fingerprint_frame(b)


def test_nan_values_are_handled_deterministically():
    df = pd.DataFrame({"x": [1.0, np.nan]})
    assert fingerprint_frame(df) == fingerprint_frame(df.copy())


def test_param_fingerprint_is_order_independent():
    assert fingerprint_params({"p": 1, "q": 2}) == fingerprint_params({"q": 2, "p": 1})
