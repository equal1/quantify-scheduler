# -----------------------------------------------------------------------------
# Description:    Schedule helper functions.
# Repository:     https://gitlab.com/quantify-os/quantify-scheduler
# Copyright (C) Qblox BV & Orange Quantum Systems Holding BV (2020-2021)
# -----------------------------------------------------------------------------
from __future__ import annotations

import inspect
from functools import partial
from typing import Any, Dict, List
from abc import ABC

try:
    from typing import Protocol as _Protocol
except ImportError:
    Protocol = ABC
else:
    Protocol = _Protocol

import numpy as np
import quantify.utilities.general as general

import quantify.scheduler.waveforms as waveforms
from quantify.scheduler.helpers import schedule as schedule_helpers
from quantify.scheduler import math
from quantify.scheduler import types


class GetWaveformPartial(Protocol):  # typing.Protocol
    """
    Protocol type definition class for the get_waveform
    partial function.
    """

    def __call__(self, sampling_rate: int) -> np.ndarray:
        """
        Execute partial get_waveform function.

        Parameters
        ----------
        sampling_rate :
            The waveform sampling rate.

        Returns
        -------
        np.ndarray
            The waveform array.
        """


def resize_waveforms(waveforms_dict: Dict[int, np.ndarray], granularity: int):
    """
    Resizes the waveforms to a multiple of the given
    granularity.

    Parameters
    ----------
    waveforms_dict :
        The waveforms dictionary.
    granularity :
        The granularity.
    """
    # Modify the list while iterating to avoid copies
    for pulse_id in waveforms_dict:
        waveforms_dict[pulse_id] = resize_waveform(
            waveforms_dict[pulse_id], granularity
        )


def resize_waveform(waveform: np.array, granularity: int) -> np.array:
    """
    Returns the waveform in a size that is a modulo of the given granularity.

    Parameters
    ----------
    waveform :
        The waveform array.
    granularity :
        The waveform granularity.

    Returns
    -------
    np.array
        The resized waveform with a length equal to
            `mod(len(waveform), granularity) == 0`.
    """
    size: int = len(waveform)
    if size == 0:
        return np.zeros(granularity)

    if size % granularity == 0:
        return waveform

    remainder = math.closest_number_ceil(size, granularity) - size

    # Append the waveform with the remainder zeros
    return np.concatenate([waveform, np.zeros(remainder)])


def get_waveform(
    pulse_info: Dict[str, Any],
    sampling_rate: int,
) -> np.ndarray:
    """
    Returns the waveform of a pulse_info dictionary.

    Parameters
    ----------
    pulse_info :
        The pulse_info dictionary.
    sampling_rate :
        The sample rate of the waveform.

    Returns
    -------
    np.ndarray
        The waveform.
    """
    t: np.ndarray = np.arange(0, 0 + pulse_info["duration"], 1 / sampling_rate)
    wf_func: str = pulse_info["wf_func"]
    waveform: np.ndarray = exec_waveform_function(wf_func, t, pulse_info)

    return waveform


def get_waveform_by_pulseid(
    schedule: types.Schedule,
) -> Dict[int, GetWaveformPartial]:
    """
    Returns a lookup dictionary of pulse_id and
    respectively its partial waveform function.

    The keys are pulse info ids while the values are partial functions of
    :func:`~quantify.scheduler.helpers.schedule.get_waveform`. Executing
    the waveform will return a :class:`numpy.ndarray`.

    Parameters
    ----------
    schedule :
        The schedule.

    Returns
    -------
    Dict[int, GetWaveformPartial]
    """
    pulseid_waveformfn_dict: Dict[int, GetWaveformPartial] = dict()
    for t_constr in schedule.timing_constraints:
        operation = schedule.operations[t_constr["operation_hash"]]
        for pulse_info in operation["pulse_info"]:
            pulse_id = schedule_helpers.get_pulse_uuid(pulse_info)
            if pulse_id in pulseid_waveformfn_dict:
                # Unique waveform already populated in the dictionary.
                continue

            pulseid_waveformfn_dict[pulse_id] = partial(
                get_waveform, pulse_info=pulse_info
            )

        for acq_info in operation["acquisition_info"]:
            for pulse_info in acq_info["waveforms"]:
                pulse_id = schedule_helpers.get_pulse_uuid(pulse_info)
                pulseid_waveformfn_dict[pulse_id] = partial(
                    get_waveform, pulse_info=pulse_info
                )

    return pulseid_waveformfn_dict


def exec_waveform_partial(
    pulse_id: int,
    pulseid_waveformfn_dict: Dict[int, GetWaveformPartial],
    sampling_rate: int,
) -> np.ndarray:
    """
    Returns the result of the partial waveform function.

    Parameters
    ----------
    pulse_id :
        The pulse uuid.
    pulseid_waveformfn_dict :
        The partial waveform lookup dictionary.
    sampling_rate :
        The sampling rate.

    Returns
    -------
    np.ndarray
        The waveform array.
    """
    # Execute partial function get_waveform that already has
    # 'pulse_info' assigned. The following method execution
    # adds the missing required parameters.
    waveform_fn: GetWaveformPartial = pulseid_waveformfn_dict[pulse_id]
    waveform: np.ndarray = waveform_fn(
        sampling_rate=sampling_rate,
    )

    return waveform


def exec_waveform_function(wf_func: str, t: np.ndarray, pulse_info: dict) -> np.ndarray:
    """
    Returns the result of the pulse's waveform function.

    If the wf_func is defined outside quantify-scheduler then the
    wf_func is dynamically loaded and executed using
    :func:`~quantify.schedule.helpers.schedule.exec_custom_waveform_function`.

    Parameters
    ----------
    wf_func :
        The custom waveform function path.
    t :
        The linear timespace.
    pulse_info :
        The dictionary containing pulse information.

    Returns
    -------
    np.ndarray
        Returns the computed waveform.
    """
    whitelist: List[str] = ["square", "ramp", "soft_square", "drag"]
    fn_name: str = wf_func.split(".")[-1]
    waveform: np.ndarray = []
    if wf_func.startswith("quantify.scheduler.waveforms") and fn_name in whitelist:
        if fn_name == "square":
            waveform = waveforms.square(t=t, amp=pulse_info["amp"])
        elif fn_name == "ramp":
            waveform = waveforms.ramp(t=t, amp=pulse_info["amp"])
        elif fn_name == "soft_square":
            waveform = waveforms.soft_square(t=t, amp=pulse_info["amp"])
        elif fn_name == "drag":
            waveform = waveforms.drag(
                t=t,
                G_amp=pulse_info["G_amp"],
                D_amp=pulse_info["D_amp"],
                duration=pulse_info["duration"],
                nr_sigma=pulse_info["nr_sigma"],
                phase=pulse_info["phase"],
            )
    else:
        waveform = exec_custom_waveform_function(wf_func, t, pulse_info)

    return waveform


def exec_custom_waveform_function(
    wf_func: str, t: np.ndarray, pulse_info: dict
) -> np.ndarray:
    """
    Load and import an ambiguous waveform function from a module by string.

    The parameters of the dynamically loaded wf_func are extracted using
    :func:`inspect.signature` while the values are extracted from the
    pulse_info dictionary.

    Parameters
    ----------
    wf_func :
        The custom waveform function path.
    t :
        The linear timespace.
    pulse_info :
        The dictionary containing pulse information.

    Returns
    -------
    np.ndarray
        Returns the computed waveform.
    """
    # Load the waveform function from string
    function = general.import_func_from_string(wf_func)

    # select the arguments for the waveform function that are present
    # in pulse info
    par_map = inspect.signature(function).parameters
    wf_kwargs = {}
    for kw in par_map.keys():
        if kw in pulse_info:
            wf_kwargs[kw] = pulse_info[kw]

    # Calculate the numerical waveform using the wf_func
    return function(t=t, **wf_kwargs)