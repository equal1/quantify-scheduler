# pylint: disable=missing-class-docstring
# pylint: disable=missing-function-docstring
# pylint: disable=redefined-outer-name
# pylint: disable=missing-module-docstring
# pylint: disable=no-self-use

# Repository: https://gitlab.com/quantify-os/quantify-scheduler
# Licensed according to the LICENCE file on the master branch
"""Tests for the driver version check."""

import pytest

from qblox_instruments import build

from quantify_scheduler.backends.qblox.driver_version_check import (
    verify_qblox_instruments_version,
    SUPPORTED_DRIVER_VERSIONS,
)
from quantify_scheduler.backends.qblox.driver_version_check import DriverVersionError


def test_verify_qblox_instruments_version():
    verify_qblox_instruments_version(build.__version__)

    nonsense_version = "nonsense.driver.version"
    with pytest.raises(DriverVersionError) as wrong_version:
        verify_qblox_instruments_version(nonsense_version)
    assert (
        wrong_version.value.args[0]
        == f"The installed Qblox driver (qblox-instruments) version {nonsense_version} "
        "is not supported by backend. Please install one of the supported versions "
        f"({'; '.join(SUPPORTED_DRIVER_VERSIONS)}) in order to use this backend."
    )

    with pytest.raises(DriverVersionError) as none_error:
        verify_qblox_instruments_version(None)

    assert (
        none_error.value.args[0]
        == "Version check for Qblox driver (qblox-instruments) could not be "
        "performed. Either the package is not installed correctly or a version "
        "<0.3.2 was found."
    )