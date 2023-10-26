# Repository: https://gitlab.com/quantify-os/quantify-scheduler
# Licensed according to the LICENCE file on the main branch

"""Helper functions for debugging experiments."""

import json
import os
import sys
import traceback
import zipfile
from datetime import datetime
from os.path import dirname
from types import TracebackType
from typing import Any, Dict, Optional, Tuple, Type, Union
from uuid import uuid4

import numpy as np
import pytz
from qcodes.utils.json_utils import NumpyJSONEncoder

try:
    import tomllib  # type: ignore[reportMissingImports]
except ModuleNotFoundError:
    import tomli as tomllib

import pkg_resources
from packaging.specifiers import SpecifierSet

from quantify_core._version import __version__ as __core_version__
from quantify_core.data.handling import get_datadir, snapshot
from quantify_scheduler import CompiledSchedule, Schedule
from quantify_scheduler._version import __version__ as __scheduler_version__
from quantify_scheduler.device_under_test.quantum_device import QuantumDevice
from quantify_scheduler.device_under_test.transmon_element import BasicTransmonElement
from quantify_scheduler.instrument_coordinator.instrument_coordinator import (
    InstrumentCoordinator,
)
from quantify_scheduler.json_utils import ScheduleJSONEncoder


def _generate_diagnostics_report(  # noqa: PLR0915
    quantum_device: QuantumDevice,
    gettable_config: Dict[str, Any],
    schedule: Schedule,
    instrument_coordinator: InstrumentCoordinator,
    initialized: bool,
    compiled_schedule: Union[CompiledSchedule, None],
    acquisition_data: Union[Tuple[np.ndarray, ...], None],
    experiment_exception: Union[
        Union[
            Tuple[Type[BaseException], BaseException, TracebackType],
            Tuple[None, None, None],
        ],
        None,
    ],
) -> str:
    """
    Generate a report with the current state of an experiment for debugging.

    Returns
    -------
    :
        A path to the generated zipfile report.
    """

    def _flatten_hardware_logs_dict(
        hw_logs: dict, extracted_hw_logs: dict, prefix: Optional[str] = None
    ) -> dict:
        for key, value in hw_logs.items():
            if isinstance(value, dict):
                _flatten_hardware_logs_dict(
                    hw_logs=value, extracted_hw_logs=extracted_hw_logs, prefix=key
                )
            else:
                extracted_hw_logs[
                    f"{prefix}_{key}" if prefix is not None else f"{key}"
                ] = value
        return extracted_hw_logs

    def _get_dependency_versions() -> list:
        package_root = dirname(dirname(os.path.realpath(__file__)))
        pyproject_path = os.path.join(dirname(package_root), "pyproject.toml")

        with open(pyproject_path, "rb") as pyproject_file:
            pyproject_data = tomllib.load(pyproject_file)

        all_dependency_versions = []
        for dependency in pyproject_data["project"]["dependencies"]:
            version = (
                __core_version__
                if dependency == "quantify-core"
                else pkg_resources.get_distribution(dependency).version
            )
            all_dependency_versions.append(f"{dependency}: {version}")

        python_specs = SpecifierSet(pyproject_data["project"]["requires-python"])
        python_version = sys.version.split(" ")[0]
        all_dependency_versions.append(f"python{python_specs}: {python_version}")

        all_dependency_versions.append(f"quantify-scheduler: {__scheduler_version__}")

        return sorted(all_dependency_versions)

    report_type = "failed_initialization"

    connection_exception = None
    hardware_logs = {}
    if initialized:
        try:
            hardware_logs = instrument_coordinator.retrieve_hardware_logs()
        except:  # noqa: E722  pylint: disable=bare-except
            connection_exception = sys.exc_info()

        if experiment_exception is not None:
            report_type = (
                "failed_connection_to_hw"
                if connection_exception is not None
                else "failed_exp"
            )
        else:
            report_type = (
                "failed_hw_log_retrieval"
                if connection_exception is not None
                else "completed_exp"
            )

    report_zipfile = os.path.join(
        get_datadir(), f"diagnostics_report_{report_type}_{uuid4()}.zip"
    )

    with zipfile.ZipFile(
        report_zipfile, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as zip_file:
        timestamp = datetime.now(pytz.utc).astimezone(pytz.timezone("Europe/Amsterdam"))
        zip_file.writestr(
            "timestamp.txt",
            timestamp.strftime("%Y-%m-%d %H:%M:%S %Z%z"),
        )
        zip_file.writestr(
            "dependency_versions.json", json.dumps(_get_dependency_versions())
        )
        zip_file.writestr(
            "gettable.json", json.dumps(gettable_config, cls=NumpyJSONEncoder, indent=4)
        )

        for element_name in quantum_device.elements():
            element = quantum_device.get_element(element_name)
            if isinstance(element, BasicTransmonElement):
                element_str = json.dumps(element, cls=ScheduleJSONEncoder)
            else:
                element_str = json.dumps(
                    element.generate_device_config(), cls=NumpyJSONEncoder, indent=4
                )
            zip_file.writestr(
                os.path.join("device_elements", f"{element_name}.json"), element_str
            )

        zip_file.writestr(
            "hardware_cfg.json",
            json.dumps(
                quantum_device.generate_hardware_config(),
                cls=NumpyJSONEncoder,
                indent=4,
            ),
        )

        zip_file.writestr("schedule.json", schedule.to_json())
        if compiled_schedule is not None:
            zip_file.writestr("compiled_schedule.json", compiled_schedule.to_json())

        if initialized:
            zip_file.writestr(
                "snapshot.json",
                json.dumps(snapshot(), cls=NumpyJSONEncoder, indent=4),
            )

            if acquisition_data is not None:
                data_str = ""
                for line in acquisition_data:
                    data_str += f"{np.array2string(line)},\n"
                data_str.rstrip(",\n")
                zip_file.writestr("acquisition_data.txt", data_str)

            if hardware_logs:
                for (
                    component_name,
                    component_hardware_logs,
                ) in hardware_logs.items():
                    for log_name, hardware_log in _flatten_hardware_logs_dict(
                        hw_logs=component_hardware_logs, extracted_hw_logs={}
                    ).items():
                        zip_file.writestr(
                            os.path.join(component_name, f"{log_name}.txt"),
                            hardware_log,
                        )

        if experiment_exception is not None:
            exception_str = " ".join(traceback.format_exception(*experiment_exception))
            zip_file.writestr("error_trace.txt", exception_str)
        elif connection_exception is not None:
            # No need to print this if there is already an experiment_exception
            exception_str = " ".join(traceback.format_exception(*connection_exception))
            zip_file.writestr("connection_error_trace.txt", exception_str)

    return report_zipfile
