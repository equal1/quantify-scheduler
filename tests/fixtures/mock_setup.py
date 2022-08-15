# Repository: https://gitlab.com/quantify-os/quantify-scheduler
# Licensed according to the LICENCE file on the main branch
"""Pytest fixtures for quantify-scheduler."""

import os
import shutil
import pathlib

from typing import List

import pytest
from qcodes import Instrument

from quantify_core.data.handling import get_datadir, set_datadir

from quantify_scheduler.device_under_test.mock_setup import (
    set_up_mock_transmon_setup,
    set_up_mock_transmon_setup_legacy,
    set_standard_params_transmon,
)
from quantify_scheduler.device_under_test.quantum_device import QuantumDevice
from quantify_scheduler.device_under_test.transmon_element import BasicTransmonElement
from quantify_scheduler.schemas.examples import utils

# Test hardware mappings. Note, these will change as we are updating our hardware
# mapping for the graph based compilation.
QBLOX_HARDWARE_MAPPING = utils.load_json_example_scheme("qblox_test_mapping.json")
ZHINST_HARDWARE_MAPPING = utils.load_json_example_scheme("zhinst_test_mapping.json")


def close_instruments(instrument_names: List[str]):
    """Close all instruments in the list of names supplied."""
    for name in instrument_names:
        try:
            Instrument.find_instrument(name).close()
        except KeyError:
            pass


@pytest.fixture(scope="session", autouse=True)
def tmp_test_data_dir(tmp_path_factory):
    """
    This is a fixture which uses the pytest tmp_path_factory fixture
    and extends it by copying the entire contents of the test_data
    directory. After the test session is finished, it cleans up the temporary dir.
    """

    # disable this if you want to look at the generated datafiles for debugging.
    use_temp_dir = True
    if use_temp_dir:
        temp_data_dir = tmp_path_factory.mktemp("temp_data")
        yield temp_data_dir
        shutil.rmtree(temp_data_dir, ignore_errors=True)
    else:
        set_datadir(os.path.join(pathlib.Path.home(), "quantify_scheduler_test"))
        print(f"Data directory set to: {get_datadir()}")
        yield get_datadir()


# pylint: disable=redefined-outer-name
@pytest.fixture(scope="module", autouse=False)
def mock_setup(tmp_test_data_dir):
    """
    Returns a mock setup.

    This mock setup is created using the :code:`set_up_mock_transmon_setup_legacy`
    function from the .device_under_test.mock_setup module.

    """
    # The name of this function is not so good, and could be more specific. Moreover,
    # this it supports a legacy object to use in the tests. We should not use
    # this fixture in future tests and phase it out at some point.
    # The preferred alternative is the mock_setup_basic_transmon.
    set_datadir(tmp_test_data_dir)

    # moved to a separate module to allow using the mock_setup in tutorials.
    mock_setup = set_up_mock_transmon_setup_legacy()

    mock_instruments = {
        "meas_ctrl": mock_setup["meas_ctrl"],
        "instrument_coordinator": mock_setup["instrument_coordinator"],
        "q0": mock_setup["q0"],
        "q1": mock_setup["q1"],
        "q2": mock_setup["q2"],
        "q3": mock_setup["q3"],
        "q4": mock_setup["q4"],
        "q2-q3": mock_setup["q2-q3"],
        "quantum_device": mock_setup["quantum_device"],
    }

    yield mock_instruments

    # NB only close the instruments this fixture is responsible for to avoid
    # hard to debug side effects
    # N.B. the keys need to correspond to the names of the instruments otherwise
    # they do not close correctly. Watch out with edges (e.g., q0-q2)
    close_instruments(mock_instruments.keys())


# pylint: disable=redefined-outer-name
@pytest.fixture(scope="module", autouse=False)
def mock_setup_basic_transmon(tmp_test_data_dir):
    """
    Returns a mock setup for a basic 5-qubit transmon device.

    This mock setup is created using the :code:`set_up_mock_transmon_setup`
    function from the .device_under_test.mock_setup module.
    """

    set_datadir(tmp_test_data_dir)

    # moved to a separate module to allow using the mock_setup in tutorials.
    mock_setup = set_up_mock_transmon_setup()

    mock_instruments = {
        "meas_ctrl": mock_setup["meas_ctrl"],
        "instrument_coordinator": mock_setup["instrument_coordinator"],
        "q0": mock_setup["q0"],
        "q1": mock_setup["q1"],
        "q2": mock_setup["q2"],
        "q3": mock_setup["q3"],
        "q4": mock_setup["q4"],
        "q0-q2": mock_setup["q0-q2"],
        "q1-q2": mock_setup["q1-q2"],
        "q2-q3": mock_setup["q2-q3"],
        "q2-q4": mock_setup["q2-q4"],
        "quantum_device": mock_setup["quantum_device"],
    }

    yield mock_instruments

    # NB only close the instruments this fixture is responsible for to avoid
    # hard to debug side effects
    # they do not close correctly. Watch out with edges (e.g., q0-q2)
    # N.B. the keys need to correspond to the names of the instruments otherwise
    close_instruments(mock_instruments)


@pytest.fixture(scope="function", autouse=False)
def device_compile_config_basic_transmon(mock_setup_basic_transmon):
    """
    A config generated from a quantum device with 5 transmon qubits
    connected in a star configuration.

    The mock setup has no hardware attached to it.
    """
    # N.B. how this fixture produces the hardware config can change in the future
    # as long as it keeps doing what is described in this docstring.

    set_standard_params_transmon(mock_setup_basic_transmon)
    yield mock_setup_basic_transmon["quantum_device"].generate_compilation_config()


@pytest.fixture(scope="function", autouse=False)
def compile_config_basic_transmon_zhinst_hardware(mock_setup_basic_transmon):
    """
    A config for a quantum device with 5 transmon qubits connected in a star
    configuration controlled using Zurich Instruments Hardware.
    """
    # N.B. how this fixture produces the hardware config will change in the future
    # as we separate the config up into a more fine grained config. For now it uses
    # the old JSON files to load settings from.
    set_standard_params_transmon(mock_setup_basic_transmon)
    mock_setup_basic_transmon["quantum_device"].hardware_config(ZHINST_HARDWARE_MAPPING)

    # add the hardware config here
    yield mock_setup_basic_transmon["quantum_device"].generate_compilation_config()


@pytest.fixture(scope="function", autouse=False)
def compile_config_basic_transmon_qblox_hardware(mock_setup_basic_transmon):
    """
    A config for a quantum device with 5 transmon qubits connected in a star
    configuration controlled using Qblox Hardware.
    """
    # N.B. how this fixture produces the hardware config will change in the future
    # as we separate the config up into a more fine grained config. For now it uses
    # the old JSON files to load settings from.
    set_standard_params_transmon(mock_setup_basic_transmon)
    mock_setup_basic_transmon["quantum_device"].hardware_config(QBLOX_HARDWARE_MAPPING)

    yield mock_setup_basic_transmon["quantum_device"].generate_compilation_config()


@pytest.fixture(scope="function")
def mock_setup_basic_transmon_elements(element_names: List[str]):
    """
    Returns a mock setup consisting of QuantumDevice and BasicTransmonElements only.
    """

    quantum_device = QuantumDevice("quantum_device")

    elements = {}
    for name in element_names:
        elements[name] = BasicTransmonElement(name)
        quantum_device.add_element(elements[name])

    mock_instruments = {"quantum_device": quantum_device, **elements}
    yield mock_instruments

    close_instruments(mock_instruments)
