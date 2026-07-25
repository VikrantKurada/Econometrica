"""What importing the application actually brings with it."""

import subprocess
import sys


def test_importing_the_app_registers_every_tool():
    """The running server's tool registry must not be empty.

    Registration is an import side-effect of the five econ family packages,
    and every test module under ``tests/econ`` imports the family it
    exercises — so an in-process assertion here would pass no matter what
    ``main`` does. A subprocess importing only the app is the only way to see
    what a real `uvicorn econometrica.main:app` sees.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import econometrica.main;"
            "from econometrica.econ.registry import get_registry;"
            "print(len(get_registry().all()))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert int(result.stdout.strip()) >= 36, "the app was imported with an empty tool registry"
