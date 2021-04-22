# Repository: https://gitlab.com/quantify-os/quantify-scheduler
# Licensed according to the LICENCE file on the master branch
"""Compiler backend for Qblox hardware."""

from __future__ import annotations

import inspect
import os
import sys
import json
from abc import ABCMeta, abstractmethod
from collections import UserDict, defaultdict, deque
from collections.abc import Iterable
from contextlib import contextmanager
from typing import TYPE_CHECKING, Dict, Any, Optional, List, Tuple, Union, Set, Callable

from columnar import columnar
from columnar.exceptions import TableOverflowError
from qcodes.utils.helpers import NumpyJSONEncoder

import numpy as np
from quantify.scheduler.helpers.schedule import get_total_duration
from quantify.scheduler.helpers.waveforms import normalize_waveform_data
from quantify.data.handling import get_datadir, gen_tuid
from quantify.utilities.general import make_hash, without, import_func_from_string

from quantify.scheduler.backends.types.qblox import (
    PulsarSettings,
    SequencerSettings,
    MixerCorrections,
    OpInfo,
    QASMRuntimeSettings,
)

from quantify.scheduler.types import Schedule


# ---------- utility functions ----------
def _sanitize_file_name(filename: str) -> str:
    """
    Takes a str representing a filename and removes invalid characters
    by replacing it with an underscore character.

    e.g. invalid:file?name.json -> invalid_file_name.json

    Characters that are considered invalid: ',<>:"/\\|!?* '

    Parameters
    ----------
    filename
        The str representing the filename

    Returns
    -------
    :
        The sanitized filename
    """
    invalid = ',<>:"/\\|!?* '
    sanitized_fn = filename
    for char in invalid:
        sanitized_fn = sanitized_fn.replace(char, "_")
    return sanitized_fn


def modulate_waveform(
    t: np.ndarray, envelope: np.ndarray, freq: float, t0: float = 0
) -> np.ndarray:
    """
    Generates a (single sideband) modulated waveform from a given envelope by
    multiplying it with a complex exponential.

    .. math::
        z_{mod} (t) = z (t) \cdot e^{2\pi i f (t+t_0)}

    The signs are chosen such that the frequencies follow the relation RF = LO + IF for
    LO, IF > 0.

    Parameters
    ----------
    t:
        A numpy array with time values
    envelope:
        The complex-valued envelope of the modulated waveform
    freq:
        The frequency of the modulation
    t0:
        Time offset for the modulation

    Returns
    -------
    :
        The modulated waveform
    """
    modulation = np.exp(1.0j * 2 * np.pi * freq * (t + t0))
    return envelope * modulation


def _generate_waveform_data(data_dict: dict, sampling_rate: float) -> np.ndarray:
    """
    Generates an array using the parameters specified in `data_dict`.

    Parameters
    ----------
    data_dict:
        The dictionary that contains the values needed to parameterize the
        waveform. `data_dict['wf_func']` is then called to calculate the values.
    sampling_rate:
        The sampling rate used to generate the time axis values.

    Returns
    -------
    :
        The (possibly complex) values of the generated waveform
    """
    time_duration = data_dict["duration"]
    t = np.linspace(0, time_duration, int(time_duration * sampling_rate))

    func = import_func_from_string(data_dict["wf_func"])
    par_map = inspect.signature(func).parameters.keys()

    data_dict_keys = set(data_dict.keys())
    parameters_supplied = data_dict_keys.intersection(set(par_map))

    kwargs = {key: data_dict[key] for key in parameters_supplied}
    wf_data = func(t=t, **kwargs)
    if not isinstance(wf_data, np.ndarray):
        wf_data = np.array(wf_data)

    return wf_data


def generate_ext_local_oscillators(
    total_play_time: float, hardware_cfg: Dict[str, Any]
) -> Dict[str, LocalOscillator]:
    """
    Traverses the `hardware_cfg` dict and extracts the used local oscillators.
    `LocalOscillator` objects are instantiated for each LO and the `lo_freq` is
    assigned if specified.

    Parameters
    ----------
    total_play_time:
        Total time the schedule is played for, not counting repetitions.
    hardware_cfg:
        Hardware mapping dictionary

    Returns
    -------
    :
        A dictionary with the names of the devices as keys and compiler
        objects for the local oscillators as values.
    """
    # TODO more generic with get_inner_dicts_containing_key?
    lo_dict = dict()
    for device in hardware_cfg.values():
        if not isinstance(device, dict):  # is not a device
            continue

        for io_cfg in device.values():
            if not isinstance(io_cfg, dict):  # is not a in/output
                continue

            if "lo_name" in io_cfg:
                lo_name = io_cfg["lo_name"]
                if lo_name not in lo_dict:
                    lo_obj = LocalOscillator(
                        lo_name,
                        total_play_time,
                    )
                    lo_dict[lo_name] = lo_obj

                if "lo_freq" in io_cfg:
                    lo_dict[lo_name].assign_frequency(io_cfg["lo_freq"])

    return lo_dict


def _calculate_total_play_time(schedule: Schedule) -> float:
    """
    Calculates the total time the schedule has to be executed on the hardware, not
    accounting for repetitions. Effectively, this is the maximum of the end times of
    the pulses and acquisitions.

    Parameters
    ----------
    schedule:
        The quantify schedule object of which we want the total execution time

    Returns
    -------
    :
        Total play time in seconds
    """
    end_times = list()
    for time_constraint in schedule.timing_constraints:
        pulse_id = time_constraint["operation_hash"]
        operation = schedule.operations[pulse_id]
        end_time = operation.duration + time_constraint["abs_time"]

        end_times.append(end_time)

    return np.max(end_times)


# pylint disable:invalid-name
def find_inner_dicts_containing_key(d: Union[Dict, UserDict], key: Any) -> List[dict]:
    """
    Generates a list of the first dictionaries encountered that contain a certain key,
    in a complicated dictionary with nested dictionaries or Iterables.

    This is achieved by recursively traversing the nested structures until the key is
    found, which is then appended to a list.

    Parameters
    ----------
    d:
        The dictionary to traverse.
    key:
        The key to search for.

    Returns
    -------
    :
        A list containing all the inner dictionaries containing the specified key.
    """
    dicts_found = list()
    if isinstance(d, dict):
        if key in d:
            dicts_found.append(d)
    for val in d.values():
        if isinstance(val, (dict, UserDict)):
            dicts_found.extend(find_inner_dicts_containing_key(val, key))
        elif isinstance(val, Iterable) and not isinstance(val, str):
            for i_item in val:
                try:
                    dicts_found.extend(find_inner_dicts_containing_key(i_item, key))
                # having a list that contains something other than a dict can cause an
                # AttributeError on d, but this should be ignored anyway
                except AttributeError:
                    continue
        else:
            continue
    return dicts_found


# pylint disable:invalid-name
def find_all_port_clock_combinations(d: Union[Dict, UserDict]) -> List[Tuple[str, str]]:
    """
    Generates a list with all port and clock combinations found in a dictionary with
    nested structures. Traversing the dictionary is done using the
    `find_inner_dicts_containing_key` function.

    Parameters
    ----------
    d:
        The dictionary to traverse.

    Returns
    -------
    :
        A list containing tuples representing the port and clock combinations found
        in the dictionary.
    """
    port_clocks = list()
    dicts_with_port = find_inner_dicts_containing_key(d, "port")
    for inner_dict in dicts_with_port:
        if "port" in inner_dict:
            port = inner_dict["port"]
            if port is None:
                continue
            if "clock" not in inner_dict:
                raise AttributeError(f"Port {inner_dict['port']} missing clock")
            clock = inner_dict["clock"]
            port_clocks.append((port, clock))
    return port_clocks


def generate_port_clock_to_device_map(
    mapping: Dict[str, Any]
) -> Dict[Tuple[str, str], str]:
    """
    Generates a mapping which specifies which port-clock combinations belong to which
    device.

    .. note::
        The same device may contain multiple port-clock combinations, but each
        port-clock combination may only occur once.

    Parameters
    ----------
    mapping:
        The hardware mapping config.

    Returns
    -------
    :
        A dictionary with as key a tuple representing a port-clock combination, and
        as value the name of the device. Note that multiple port-clocks may point to
        the same device.
    """

    portclock_map = dict()
    for device_name, device_info in mapping.items():
        if not isinstance(device_info, dict):
            continue

        portclocks = find_all_port_clock_combinations(device_info)

        for portclock in portclocks:
            portclock_map[portclock] = device_name

    return portclock_map


def _generate_waveform_dict(
    waveforms_complex: Dict[int, np.ndarray]
) -> Dict[str, dict]:
    """
    Takes a dictionary with complex waveforms and generates a new dictionary with
    real valued waveforms with a unique index, as required by the hardware.

    Parameters
    ----------
    waveforms_complex:
        Dictionary containing the complex waveforms. Keys correspond to a unique
        identifier, value is the complex waveform.

    Returns
    -------
    :
        A dictionary with as key the unique name for that waveform, as value another
        dictionary containing the real-valued data (list) as well as a unique index.
        Note that the index of the Q waveform is always the index of the I waveform
        +1.

    Examples
    --------

    .. code-block::

        complex_waveforms = {12345: np.array([1, 2])}
        _generate_waveform_dict(complex_waveforms)
        {
            "12345_I": {"data": [1, 2], "index": 0},
            "12345_Q": {"data": [0, 0], "index": 1}
        }
    """
    wf_dict = dict()
    for idx, (uuid, complex_data) in enumerate(waveforms_complex.items()):
        name_i, name_q = Pulsar_sequencer_base.generate_waveform_names_from_uuid(uuid)
        to_add = {
            name_i: {"data": complex_data.real.tolist(), "index": 2 * idx},
            name_q: {"data": complex_data.imag.tolist(), "index": 2 * idx + 1},
        }
        wf_dict.update(to_add)
    return wf_dict


# ---------- classes ----------
class InstrumentCompiler(metaclass=ABCMeta):
    """
    Abstract base class that defines a generic instrument compiler. The subclasses that
    inherit from this are meant to implement the compilation steps needed to compile the
    lists of `OpInfo` representing the pulse and acquisition info to device specific
    instructions.

    For each device that needs to be part of the compilation process such a
    `InstrumentCompiler` should be implemented.
    """

    def __init__(
        self,
        name: str,
        total_play_time: float,
        hw_mapping: Optional[Dict[str, Any]] = None,
    ):
        """
        Constructor for an InstrumentCompiler object.

        Parameters
        ----------
        name:
            Name of the `QCoDeS` instrument this compiler object corresponds to.
        total_play_time:
            Total time execution of the schedule should go on for. This parameter is
            used to ensure that the different devices, potentially with different clock
            rates, can work in a synchronized way when performing multiple executions of
            the schedule.
        hw_mapping:
            The hardware configuration dictionary for this specific device. This is one
            of the inner dictionaries of the overall hardware config.
        """
        self.name = name
        self.total_play_time = total_play_time
        self.hw_mapping = hw_mapping
        self._pulses = defaultdict(list)
        self._acquisitions = defaultdict(list)

    def add_pulse(self, port: str, clock: str, pulse_info: OpInfo):
        """
        Assigns a certain pulse to this device.

        Parameters
        ----------
        port:
            The port the pulse needs to be sent to.
        clock:
            The clock for modulation of the pulse. Can be a BasebandClock.
        pulse_info:
            Data structure containing all the information regarding this specific pulse
            operation.

        Returns
        -------

        """
        self._pulses[(port, clock)].append(pulse_info)

    def add_acquisition(self, port: str, clock: str, acq_info: OpInfo):
        """
        Assigns a certain acquisition to this device.

        Parameters
        ----------
        port:
            The port the pulse needs to be sent to.
        clock:
            The clock for modulation of the pulse. Can be a BasebandClock.
        acq_info:
            Data structure containing all the information regarding this specific
            acquisition operation.

        Returns
        -------

        """
        self._acquisitions[(port, clock)].append(acq_info)

    @property
    def portclocks_with_data(self) -> Set[Tuple[str, str]]:
        """
        All the port-clock combinations associated with at least one pulse or
        acquisition.

        Returns
        -------
        :
            A set containing all the port-clock combinations
        """
        portclocks_used = set()
        portclocks_used.update(self._pulses.keys())
        portclocks_used.update(self._acquisitions.keys())
        return portclocks_used

    @abstractmethod
    def compile(self, repetitions: int = 1) -> Any:
        """
        An abstract method that should be overridden by a subclass to implement the
        actual compilation. Method turns the pulses and acquisitions added to the device
        into device specific instructions.

        Parameters
        ----------
        repetitions:
            Number of times execution the schedule is repeated

        Returns
        -------
        :
            A data structure representing the compiled program. The type is
            dependent on implementation.
        """


class LocalOscillator(InstrumentCompiler):
    """
    Implementation of an `InstrumentCompiler` that compiles for a generic LO. The main
    difference between this class and the other compiler classes is that it doesn't take
    pulses and acquisitions.
    """

    def __init__(
        self,
        name: str,
        total_play_time: float,
        lo_freq: Optional[int] = None,
    ):
        """
        Constructor for a local oscillator compiler.

        Parameters
        ----------
        name:
            QCoDeS name of the device it compiles for.
        total_play_time:
            Total time execution of the schedule should go on for. This parameter is
            used to ensure that the different devices, potentially with different clock
            rates, can work in a synchronized way when performing multiple executions of
            the schedule.
        lo_freq:
            LO frequency it needs to be set to. Either this is passed to the constructor
            or set later in the compilation process, in case the LO frequency is not
            initially given and needs to be calculated.
        """
        super().__init__(name, total_play_time)
        self._lo_freq = lo_freq

    def assign_frequency(self, freq: float):
        """
        Sets the lo frequency for this device if no frequency is specified, but raises
        an exception otherwise.

        Parameters
        ----------
        freq:
            The frequency to set it to.

        Returns
        -------

        Raises
        -------
        ValueError
            Occurs when a frequency has been previously set and attempting to set the
            frequency to a different value than what it is currently set to. This would
            indicate an invalid configuration in the hardware mapping.
        """
        if self._lo_freq is not None:
            if freq != self._lo_freq:
                raise ValueError(
                    f"Attempting to set LO {self.name} to frequency {freq}, "
                    f"while it has previously already been set to {self._lo_freq}!"
                )
        self._lo_freq = freq

    @property
    def frequency(self) -> float:
        """
        Getter for the frequency.

        Returns
        -------
        :
            The current frequency
        """
        return self._lo_freq

    def compile(self, repetitions: int = 1) -> Dict[str, Any]:
        """
        Compiles the program for the LO control stack component.

        Parameters
        ----------
        repetitions:
            Number of times execution the schedule is repeated

        Returns
        -------
        :
            Dictionary containing all the information the cs component needs to set the
            parameters appropriately.
        """
        return {"lo_freq": self._lo_freq}


# ---------- utility classes ----------
# pylint: disable=too-few-public-methods
# pylint disable=invalid-name
class PulsarInstructions:
    """
    Class that holds all the string literals that are valid instructions that can be
    executed by the sequencer.
    """

    # Control
    ILLEGAL = "illegal"
    STOP = "stop"
    NOP = "nop"
    NEW_LINE = ""
    # Jumps
    JUMP = "jmp"
    LOOP = "loop"
    JUMP_GREATER_EQUALS = "jge"
    JUMP_LESS_EQUALS = "jle"
    # Arithmetic
    MOVE = "move"
    NOT = "not"
    ADD = "add"
    SUB = "sub"
    AND = "and"
    OR = "or"
    XOR = "xor"
    ARITHMETIC_SHIFT_LEFT = "asl"
    ARITHMETIC_SHIFT_RIGHT = "asr"
    # Real-time pipeline instructions
    SET_MARKER = "set_mrk"
    PLAY = "play"
    ACQUIRE = "acquire"
    WAIT = "wait"
    WAIT_SYNC = "wait_sync"
    WAIT_TRIGGER = "wait_trigger"
    UPDATE_PARAMETERS = "upd_param"
    SET_AWG_GAIN = "set_awg_gain"
    SET_ACQ_GAIN = "set_acq_gain"
    SET_AWG_OFFSET = "set_awg_offs"
    SET_ACQ_OFFSET = "set_acq_offs"
    SET_NCO_PHASE = "set_ph"
    SET_NCO_PHASE_OFFSET = "set_ph_delta"


class QASMProgram(list):
    """
    Class that holds the compiled Q1ASM program that is to be executed by the sequencer.

    The object itself is a list which holds the instructions in order of execution. The
    instructions in turn are also lists, which hold the instruction strings themselves
    along with labels, comments and parameters.

    Apart from this the class holds some convenience functions that auto generate
    certain instructions with parameters, as well as update the elapsed time.

    Attributes
    ----------
    elapsed_time:
        The time elapsed after finishing the program in its current form. This is used
        to keep track of the overall timing and necessary waits.
    """

    elapsed_time: int = 0

    @staticmethod
    def get_instruction_as_list(
        instruction: str,
        *args: Union[int, str],
        label: Optional[str] = None,
        comment: Optional[str] = None,
    ) -> List[Union[str, int]]:
        """
        Takes an instruction with arguments, label and comment and turns it into the
        list required by the class.

        Parameters
        ----------
        instruction:
            The instruction to use. This should be one specified in `PulsarInstructions`
            or the assembler will raise an exception.
        args:
            Arguments to be passed.
        label:
            Adds a label to the line. Used for jumps and loops.
        comment:
            Optionally add a comment to the instruction.

        Returns
        -------
        :
            List that contains all the passed information in the valid format for the
            program.

        Raises
        -------
        SyntaxError
            More arguments passed than the sequencer allows.
        """
        max_args_amount = 3
        if len(args) > max_args_amount:
            raise SyntaxError(
                f"Too many arguments supplied to `get_instruction_tuple` for "
                f"instruction {instruction}."
            )
        instr_args = ",".join(str(arg) for arg in args)

        label_str = f"{label}:" if label is not None else ""
        comment_str = f"# {comment}" if comment is not None else ""
        return [label_str, instruction, instr_args, comment_str]

    def emit(self, *args, **kwargs) -> None:
        """
        Wrapper around the `get_instruction_as_list` which adds it to the program.

        Parameters
        ----------
        args:
            All arguments to pass to `get_instruction_as_list`.
        kwargs:
            All keyword arguments to pass to `get_instruction_as_list`.
        """
        self.append(self.get_instruction_as_list(*args, **kwargs))

    # --- QOL functions -----

    def auto_wait(self, wait_time: int):
        """
        Automatically emits a correct wait command. If the wait time is longer than
        allowed by the sequencer it correctly breaks it up into multiple wait
        instructions.

        Parameters
        ----------
        wait_time:
            Time to wait in ns.

        Returns
        -------

        Raises
        ------
        ValueError
            If `wait_time` <= 0
        """
        if wait_time <= 0:
            raise ValueError(
                f"Invalid wait time. Attempting to wait "
                f"for {wait_time} ns at t={self.elapsed_time}"
                f" ns."
            )

        immediate_sz = Pulsar_sequencer_base.IMMEDIATE_SZ
        if wait_time > immediate_sz:
            for _ in range(wait_time // immediate_sz):
                self.emit(
                    PulsarInstructions.WAIT, immediate_sz, comment="auto generated wait"
                )
            time_left = wait_time % immediate_sz
        else:
            time_left = int(wait_time)

        if time_left > 0:
            self.emit(PulsarInstructions.WAIT, time_left)

        self.elapsed_time += wait_time

    def wait_till_start_operation(self, operation: OpInfo):
        """
        Waits until the start of a pulse or acquisition.

        Parameters
        ----------
        operation:
            The pulse or acquisition that we want to wait for.

        Returns
        -------

        Raises
        ------
        ValueError
            If wait time < 0
        """
        start_time = self.to_pulsar_time(operation.timing)
        wait_time = start_time - self.elapsed_time
        if wait_time > 0:
            self.auto_wait(wait_time)
        elif wait_time < 0:
            raise ValueError(
                f"Invalid timing. Attempting to wait for {wait_time} "
                f"ns before {repr(operation)}. Please note that a wait time of at least"
                f" {Pulsar_sequencer_base.GRID_TIME_ns} ns is required between "
                f"operations.\nAre multiple operations being started at the same time?"
            )

    def wait_till_start_then_play(self, pulse: OpInfo, idx0: int, idx1: int):
        """
        Waits until the start of the pulse, sets the QASMRuntimeSettings and plays the
        pulse.

        Parameters
        ----------
        pulse:
            The pulse to play.
        idx0:
            Index corresponding to the I channel of the pulse in the awg dict.
        idx1:
            Index corresponding to the Q channel of the pulse in the awg dict.

        Returns
        -------

        """
        self.wait_till_start_operation(pulse)
        self.update_runtime_settings(pulse)
        self.emit(
            PulsarInstructions.PLAY, idx0, idx1, Pulsar_sequencer_base.GRID_TIME_ns
        )
        self.elapsed_time += Pulsar_sequencer_base.GRID_TIME_ns

    def wait_till_start_then_acquire(self, acquisition: OpInfo, idx0: int, idx1: int):
        """
        Waits until the start of the acquisition, then starts the acquisition.

        Parameters
        ----------
        acquisition:
            The pulse to perform.
        idx0:
            Index corresponding to the I channel of the acquisition weights in the acq
            dict.
        idx1:
            Index corresponding to the Q channel of the acquisition weights in the acq
            dict.

        Returns
        -------

        """
        self.wait_till_start_operation(acquisition)
        self.emit(
            PulsarInstructions.ACQUIRE, idx0, idx1, Pulsar_sequencer_base.GRID_TIME_ns
        )
        self.elapsed_time += Pulsar_sequencer_base.GRID_TIME_ns

    def update_runtime_settings(self, operation: OpInfo):
        """
        Adds the commands needed to correctly set the QASMRuntimeSettings.

        Parameters
        ----------
        operation:
            The pulse to prepare the settings for.

        Returns
        -------

        Notes
        -----
            Currently only the AWG gain is set correctly, as that is the only one
            actually used currently by the backend. Will be expanded in the future.
        """
        if operation.pulse_settings is None:
            raise RuntimeError(f"No real-time settings found for {repr(operation)}.")

        awg_gain_path0 = self._expand_from_normalised_range(
            operation.pulse_settings.awg_gain_0, "awg_gain_0", operation
        )
        awg_gain_path1 = self._expand_from_normalised_range(
            operation.pulse_settings.awg_gain_1, "awg_gain_1", operation
        )
        self.emit(
            PulsarInstructions.SET_AWG_GAIN,
            awg_gain_path0,
            awg_gain_path1,
            comment=f"setting gain for {operation.uuid}",
        )

    @staticmethod
    def _expand_from_normalised_range(
        val: float, param: Optional[str] = None, operation: Optional[OpInfo] = None
    ):
        """
        Takes a the value of a parameter in normalized form (abs(param) <= 1.0), and
        expands it to an integer in the appropriate range required by the sequencer.

        Parameters
        ----------
        val:
            The value of the parameter to expand.
        param:
            The name of the parameter, to make a possible exception message more
            descriptive.
        operation:
            The operation this value is expanded for, to make a possible exception
            message more descriptive.

        Returns
        -------
        :
            The expanded value of the parameter.

        Raises
        ------
        ValueError
            Parameter is not in the normalized range.
        """
        immediate_sz = Pulsar_sequencer_base.IMMEDIATE_SZ
        if np.abs(val) > 1.0:
            raise ValueError(
                f"{param} is set to {val}. Parameter must be in the range "
                f"-1.0 <= param <= 1.0 for {repr(operation)}."
            )
        return int(val * immediate_sz // 2)

    @staticmethod
    def to_pulsar_time(time: float) -> int:
        """
        Takes a float value representing a time in seconds as used by the schedule, and
        returns the integer valued time in nanoseconds that the sequencer uses.

        Parameters
        ----------
        time:
            The time to convert

        Returns
        -------
        :
            The integer valued nanosecond time
        """
        time_ns = int(np.round(time * 1e9))
        if time_ns % Pulsar_sequencer_base.GRID_TIME_ns != 0:
            raise ValueError(
                f"Attempting to use a time interval of {time_ns} ns. "
                f"Please ensure that the durations of and wait times between "
                f"operations are multiples of {Pulsar_sequencer_base.GRID_TIME_ns} ns."
            )
        return time_ns

    def __str__(self) -> str:
        """
        Returns a string representation of the program. The pulsar expects the program
        to be such a string.

        The conversion to str is done using `columnar`, which expects a list of lists,
        and turns it into a string with rows and columns corresponding to those lists.

        Returns
        -------
        :
            The string representation of the program.
        """
        try:
            return columnar(list(self), headers=None, no_borders=True)
        # running in a sphinx environment can trigger a TableOverFlowError
        except TableOverflowError:
            return columnar(
                list(self), headers=None, no_borders=True, terminal_width=120
            )

    @contextmanager
    def loop(self, register: str, label: str, repetitions: int = 1):
        """
        Defines a context manager that can be used to generate a loop in the QASM
        program.

        Parameters
        ----------
        register:
            The register to use for the loop iterator.
        label:
            The label to use for the jump.
        repetitions:
            The amount of iterations to perform.

        Returns
        -------

        Examples
        --------
        .. jupyter-execute::

            from quantify.scheduler.backends.qblox_backend import QASMProgram

            qasm = QASMProgram()
            with qasm.loop(register='R0', label='repeat', repetitions=10):
                qasm.auto_wait(100)

        This adds a loop to the program that loops 10 times over a wait of 100 ns.
        """
        comment = f"iterator for loop with label {label}"

        def gen_start():
            self.emit(PulsarInstructions.MOVE, repetitions, register, comment=comment)
            self.emit(PulsarInstructions.NEW_LINE, label=label)

        try:
            yield gen_start()
        finally:
            self.emit(PulsarInstructions.LOOP, register, f"@{label}")


# ---------- pulsar sequencer classes ----------
class Pulsar_sequencer_base(metaclass=ABCMeta):
    """
    Abstract base class that specify the compilation steps on the sequencer level. The
    distinction between Pulsar QCM and Pulsar QRM is made by the subclasses.
    """

    IMMEDIATE_SZ = pow(2, 16) - 1
    GRID_TIME_ns = 4
    SAMPLING_RATE = 1_000_000_000  # 1GS/s

    def __init__(
        self,
        parent: Pulsar_base,
        name: str,
        portclock: Tuple[str, str],
        modulation_freq: Optional[float] = None,
    ):
        """
        Constructor for the sequencer compiler.

        Parameters
        ----------
        parent:
            A reference to the parent instrument this sequencer belongs to.
        name:
            Name of the sequencer. This is supposed to match "seq{index}".
        portclock:
            Tuple that specifies the unique port and clock combination for this
            sequencer. The first value is the port, second is the clock.
        modulation_freq:
            The frequency used for modulation. This can either be passed in the
            constructor, or assigned in a later stage using `assign_frequency`.
        """
        self.parent = parent
        self._name = name
        self.port = portclock[0]
        self.clock = portclock[1]
        self.pulses: List[OpInfo] = list()
        self.acquisitions: List[OpInfo] = list()
        self._settings = SequencerSettings(
            nco_en=False, sync_en=True, modulation_freq=modulation_freq
        )
        self.mixer_corrections = None

    @property
    def portclock(self) -> Tuple[str, str]:
        """
        A tuple containing the unique port and clock combination for this sequencer.

        Returns
        -------
        :
            The portclock
        """
        return self.port, self.clock

    @property
    def modulation_freq(self) -> float:
        """
        The frequency used for modulation of the pulses.

        Returns
        -------
        :
            The frequency
        """
        return self._settings.modulation_freq

    @property
    def settings(self) -> SequencerSettings:
        """
        Gives the current settings.

        Returns
        -------
        :
            The settings set to this sequencer.
        """
        return self._settings

    @property
    def name(self):
        """
        The name assigned to this specific sequencer.

        Returns
        -------
        str
            The name.
        """
        return self._name

    @property
    @abstractmethod
    def AWG_OUTPUT_VOLT(self) -> float:
        """
        The output range in volts. This is to be overridden by the subclass to account
        for the differences between a QCM and a QRM.

        Returns
        -------
        :
            The output range in volts.
        """
        pass

    @property
    def has_data(self):
        """
        Whether or not the sequencer has any data (meaning pulses or acquisitions)
        assigned to it or not.

        Returns
        -------
        :
            Has data been assigned to this sequencer?
        """
        return len(self.acquisitions) > 0 or len(self.pulses) > 0

    def assign_frequency(self, freq: float):
        """
        Assigns a modulation frequency to the sequencer.

        Parameters
        ----------
        freq:
            The frequency to be used for modulation.

        Returns
        -------

        Raises
        ------
        ValueError
            Attempting to set the modulation frequency to a new value even though a
            value has been previously assigned.
        """
        if self._settings.modulation_freq != freq:
            if self._settings.modulation_freq is not None:
                raise ValueError(
                    f"Attempting to set the modulation frequency of {self._name} of "
                    f"{self.parent.name} to {freq}, while it has previously been set "
                    f"to {self._settings.modulation_freq}."
                )
        self._settings.modulation_freq = freq

    def _generate_awg_dict(self) -> Dict[str, Any]:
        """
        Generates the dictionary that corresponds that contains the awg waveforms in the
        format accepted by the driver.

        Notes
        -----
        The final dictionary to be included in the json that is uploaded to the pulsar
        is of the form:

        .. code-block::

            program
            awg
                waveform_name
                    data
                    index
            acq
                waveform_name
                    data
                    index

        This function generates the awg dictionary.

        Returns
        -------
        :
            The awg dictionary

        Raises
        ------
        ValueError
            I or Q amplitude is being set outside of maximum range.
        """
        waveforms_complex = dict()
        for pulse in self.pulses:
            # FIXME: Most of this is unnecessary but requires
            #  that we change how we deal with QASMRuntimeSettings
            raw_wf_data = _generate_waveform_data(
                pulse.data, sampling_rate=self.SAMPLING_RATE
            )
            raw_wf_data = self._apply_corrections_to_waveform(
                raw_wf_data, pulse.duration, pulse.timing
            )
            raw_wf_data, amp_i, amp_q = normalize_waveform_data(raw_wf_data)
            if np.abs(amp_i) > self.AWG_OUTPUT_VOLT:
                raise ValueError(
                    f"Attempting to set amplitude to invalid value. "
                    f"Maximum voltage range is +-{self.AWG_OUTPUT_VOLT} V for "
                    f"{self.__class__.__name__}.\n"
                    f"{amp_i} V is set as amplitude for the I channel for "
                    f"{repr(pulse)}"
                )
            if np.abs(amp_q) > self.AWG_OUTPUT_VOLT:
                raise ValueError(
                    f"Attempting to set amplitude to invalid value. "
                    f"Maximum voltage range is +-{self.AWG_OUTPUT_VOLT} V for "
                    f"{self.__class__.__name__}.\n"
                    f"{amp_q} V is set as amplitude for the Q channel for "
                    f"{repr(pulse)}"
                )
            pulse.pulse_settings = QASMRuntimeSettings(
                awg_gain_0=amp_i / self.AWG_OUTPUT_VOLT,
                awg_gain_1=amp_q / self.AWG_OUTPUT_VOLT,
            )
            if pulse.uuid not in waveforms_complex:
                waveforms_complex[pulse.uuid] = raw_wf_data
        return _generate_waveform_dict(waveforms_complex)

    def _generate_acq_dict(self) -> Dict[str, Any]:
        """
        Generates the dictionary that corresponds that contains the acq weights
        waveforms in the format accepted by the driver.

        Notes
        -----
        The final dictionary to be included in the json that is uploaded to the pulsar
        is of the form:

        .. code-block::

            program
            awg
                waveform_name
                    data
                    index
            acq
                waveform_name
                    data
                    index

        This function generates the acq dictionary.

        Returns
        -------
        :
            The acq dictionary

        Raises
        ------
        NotImplementedError
            Currently, only two one dimensional waveforms can be used as acquisition
            weights. This exception is raised when either or both waveforms contain
            both a real and imaginary part.
        """
        waveforms_complex = dict()
        for acq in self.acquisitions:
            if acq.uuid not in waveforms_complex:
                raw_wf_data_real = _generate_waveform_data(
                    acq.data["waveforms"][0], sampling_rate=self.SAMPLING_RATE
                )
                raw_wf_data_imag = _generate_waveform_data(
                    acq.data["waveforms"][1], sampling_rate=self.SAMPLING_RATE
                )
                self._settings.duration = len(raw_wf_data_real)
                if not (
                    np.all(np.isreal(raw_wf_data_real))
                    and np.all(np.isreal(1.0j * raw_wf_data_imag))
                ):  # since next step will break if either is complex
                    raise NotImplementedError(
                        f"Complex weights not implemented. Please use two 1d "
                        f"real-valued weights. Exception was triggered because of "
                        f"{repr(acq)}."
                    )
                waveforms_complex[acq.uuid] = raw_wf_data_real + raw_wf_data_imag
        return _generate_waveform_dict(waveforms_complex)

    def _apply_corrections_to_waveform(
        self, waveform_data: np.ndarray, time_duration: float, t0: Optional[float] = 0
    ) -> np.ndarray:
        """
        Applies all the needed pre-processing on the waveform data. This includes mixer
        corrections and modulation.

        Parameters
        ----------
        waveform_data:
            The data to correct.
        time_duration:
            Total time is seconds that the waveform is used.
        t0:
            The start time of the pulse/acquisition. This is used for instance to make
            the make the phase change continuously when the start time is not zero.

        Returns
        -------
        :
            The waveform data after applying all the transformations.
        """
        t = np.linspace(t0, time_duration + t0, int(time_duration * self.SAMPLING_RATE))
        corrected_wf = modulate_waveform(t, waveform_data, self.modulation_freq)
        if self.mixer_corrections is not None:
            corrected_wf = self.mixer_corrections.correct_skewness(corrected_wf)
        return corrected_wf

    def update_settings(self):
        """
        Updates the sequencer settings to set all parameters that are determined by the
        compiler. Currently, this only changes the offsets based on the mixer
        calibration parameters.

        Returns
        -------

        """
        if self.mixer_corrections is not None:
            self._settings.awg_offset_path_0 = (
                self.mixer_corrections.offset_I / self.AWG_OUTPUT_VOLT
            )
            self._settings.awg_offset_path_1 = (
                self.mixer_corrections.offset_Q / self.AWG_OUTPUT_VOLT
            )

    @staticmethod
    def generate_waveform_names_from_uuid(uuid: Any) -> Tuple[str, str]:
        """
        Generates names for the I and Q parts of the complex waveform based on a unique
        identifier for the pulse/acquisition.

        Parameters
        ----------
        uuid
            A unique identifier for a pulse/acquisition.

        Returns
        -------
        :
            Name for the I waveform.
        :
            Name for the Q waveform.
        """
        return f"{str(uuid)}_I", f"{str(uuid)}_Q"

    # pylint -ignore=too-many-locals
    # pylint -ignore=too-many-arguments
    @classmethod
    def generate_qasm_program(
        cls,
        total_sequence_time: float,
        pulses: Optional[List[OpInfo]] = None,
        awg_dict: Optional[Dict[str, Any]] = None,
        acquisitions: Optional[List[OpInfo]] = None,
        acq_dict: Optional[Dict[str, Any]] = None,
        repetitions: Optional[int] = 1,
    ) -> str:
        """
        Generates a QASM program for a sequencer. Requires the awg and acq dicts to
        already have been generated.

        Example of a program generated by this function:

        .. code-block::

                    wait_sync     4
                    set_mrk       1
                    move          10,R0         # iterator for loop with label start
            start:
                    wait          4
                    set_awg_gain  22663,10206  # setting gain for 9056793381316377208
                    play          0,1,4
                    wait          176
                    loop          R0,@start
                    set_mrk       0
                    upd_param     4
                    stop


        Parameters
        ----------
        total_sequence_time:
            Total time the program needs to play for. If the sequencer would be done
            before this time, a wait is added at the end to ensure synchronization.
        pulses:
            A list containing all the pulses that are to be played.
        awg_dict:
            Dictionary containing the pulse waveform data and the index that is assigned
            to the I and Q waveforms, as generated by the `generate_awg_dict` function.
            This is used to extract the relevant indexes when adding a play instruction.
        acquisitions:
            A list containing all the acquisitions that are to be performed.
        acq_dict:
            Dictionary containing the acquisition waveform data and the index that is
            assigned to the I and Q waveforms, as generated by the `generate_acq_dict`
            function. This is used to extract the relevant indexes when adding an
            acquire instruction.
        repetitions:
            Number of times to repeat execution of the schedule.

        Returns
        -------
        :
            The generated QASM program.
        """
        loop_label = "start"
        loop_register = "R0"

        qasm = QASMProgram()
        # program header
        qasm.emit(PulsarInstructions.WAIT_SYNC, cls.GRID_TIME_ns)
        qasm.emit(PulsarInstructions.SET_MARKER, 1)

        # program body
        pulses = list() if pulses is None else pulses
        acquisitions = list() if acquisitions is None else acquisitions
        op_list = pulses + acquisitions
        op_list = sorted(op_list, key=lambda p: (p.timing, p.is_acquisition))

        with qasm.loop(
            label=loop_label, register=loop_register, repetitions=repetitions
        ):
            op_queue = deque(op_list)
            while len(op_queue) > 0:
                operation = op_queue.popleft()
                if operation.is_acquisition:
                    idx0, idx1 = cls.get_indices_from_wf_dict(operation.uuid, acq_dict)
                    qasm.wait_till_start_then_acquire(operation, idx0, idx1)
                else:
                    idx0, idx1 = cls.get_indices_from_wf_dict(operation.uuid, awg_dict)
                    qasm.wait_till_start_then_play(operation, idx0, idx1)

            end_time = qasm.to_pulsar_time(total_sequence_time)
            wait_time = end_time - qasm.elapsed_time
            if wait_time <= 0:
                raise RuntimeError(
                    f"Invalid timing detected, attempting to insert wait "
                    f"of {wait_time} ns. The total duration of the "
                    f"schedule is {end_time} but {qasm.elapsed_time} ns "
                    f"already processed."
                )
            qasm.auto_wait(wait_time)

        # program footer
        qasm.emit(PulsarInstructions.SET_MARKER, 0)
        qasm.emit(PulsarInstructions.UPDATE_PARAMETERS, cls.GRID_TIME_ns)
        qasm.emit(PulsarInstructions.STOP)
        return str(qasm)

    @staticmethod
    def get_indices_from_wf_dict(uuid: int, wf_dict: Dict[str, Any]) -> Tuple[int, int]:
        """
        Takes a awg_dict or acq_dict and extracts the waveform indices based off of the
        uuid of the pulse/acquisition.

        Parameters
        ----------
        uuid:
            The unique identifier of the pulse/acquisition.
        wf_dict:
            The awg or acq dict that holds the waveform data and indices.

        Returns
        -------
        :
            Index of the I waveform.
        :
            Index of the Q waveform.
        """
        name_real, name_imag = Pulsar_sequencer_base.generate_waveform_names_from_uuid(
            uuid
        )
        return wf_dict[name_real]["index"], wf_dict[name_imag]["index"]

    @staticmethod
    def _generate_waveforms_and_program_dict(
        program: str,
        awg_dict: Dict[str, Any],
        acq_dict: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Generates the full waveforms and program dict that is to be uploaded to the
        sequencer from the program string and the awg and acq dicts, by combining them
        and assigning the appropriate keys.

        Parameters
        ----------
        program:
            The compiled QASM program as a string.
        awg_dict:
            The dictionary containing all the awg data and indices. This is expected to
            be of the form generated by the `generate_awg_dict` method.
        acq_dict:
            The dictionary containing all the acq data and indices. This is expected to
            be of the form generated by the `generate_acq_dict` method.

        Returns
        -------
        :
            The combined program.
        """
        compiled_dict = dict()
        compiled_dict["program"] = program
        compiled_dict["waveforms"] = dict()
        compiled_dict["waveforms"]["awg"] = awg_dict
        if acq_dict is not None:
            compiled_dict["waveforms"]["acq"] = acq_dict
        return compiled_dict

    @staticmethod
    def _dump_waveforms_and_program_json(
        wf_and_pr_dict: Dict[str, Any], label: Optional[str] = None
    ) -> str:
        """
        Takes a combined waveforms and program dict and dumps it as a json file.

        Parameters
        ----------
        wf_and_pr_dict:
            The dict to dump as a json file.
        label:
            A label that is appended to the filename.

        Returns
        -------
        :
            The full absolute path where the json file is stored.
        """
        data_dir = get_datadir()
        folder = os.path.join(data_dir, "schedules")
        os.makedirs(folder, exist_ok=True)

        filename = (
            f"{gen_tuid()}.json" if label is None else f"{gen_tuid()}_{label}.json"
        )
        filename = _sanitize_file_name(filename)
        file_path = os.path.join(folder, filename)

        with open(file_path, "w") as file:
            json.dump(wf_and_pr_dict, file, cls=NumpyJSONEncoder, indent=4)

        return file_path

    def compile(self, repetitions: int = 1) -> Optional[Dict[str, Any]]:
        """
        Performs the full sequencer level compilation based on the assigned data and
        settings. If no data is assigned to this sequencer, the compilation is skipped
        and None is returned instead.

        Parameters
        ----------
        repetitions:
            Number of times execution the schedule is repeated

        Returns
        -------
        :
            The compiled program. If no data is assigned to this sequencer, the
            compilation is skipped and None is returned instead.
        """
        if not self.has_data:
            return None

        awg_dict = self._generate_awg_dict()
        acq_dict = self._generate_acq_dict() if len(self.acquisitions) > 0 else None

        qasm_program = self.generate_qasm_program(
            self.parent.total_play_time,
            self.pulses,
            awg_dict,
            self.acquisitions,
            acq_dict,
            repetitions=repetitions,
        )

        wf_and_pr_dict = self._generate_waveforms_and_program_dict(
            qasm_program, awg_dict, acq_dict
        )

        json_filename = self._dump_waveforms_and_program_json(
            wf_and_pr_dict, f"{self.port}_{self.clock}"
        )
        self.update_settings()
        settings_dict = self.settings.to_dict()
        return {"seq_fn": json_filename, "settings": settings_dict}


class QCM_sequencer(Pulsar_sequencer_base):
    """
    Subclass of Pulsar_sequencer_base that is meant to implement all the parts that are
    specific to a Pulsar QCM sequencer.

    Attributes
    ----------
    AWG_OUTPUT_VOLT:
        Voltage range of the awg output paths.
    """

    AWG_OUTPUT_VOLT = 5


class QRM_sequencer(Pulsar_sequencer_base):
    """
    Subclass of Pulsar_sequencer_base that is meant to implement all the parts that are
    specific to a Pulsar QRM sequencer.

    Attributes
    ----------
    AWG_OUTPUT_VOLT:
        Voltage range of the awg output paths.
    """

    AWG_OUTPUT_VOLT = 1


# ---------- pulsar instrument classes ----------
class Pulsar_base(InstrumentCompiler, metaclass=ABCMeta):
    """
    `InstrumentCompiler` level compiler object for a pulsar. The class is defined as an
    abstract base class since the distinction between Pulsar QRM and Pulsar QCM specific
    implementations are defined in subclasses.

    Attributes
    ----------
    OUTPUT_TO_SEQ:
        Dictionary that maps output names to specific sequencer indices. This
        implementation is temporary and will change when multiplexing is supported by
        the hardware.
    """

    OUTPUT_TO_SEQ = {"complex_output_0": 0, "complex_output_1": 1}

    def __init__(
        self,
        name: str,
        total_play_time: float,
        hw_mapping: Dict[str, Any],
    ):
        """
        Constructor function.

        Parameters
        ----------
        name:
            Name of the `QCoDeS` instrument this compiler object corresponds to.
        total_play_time:
            Total time execution of the schedule should go on for. This parameter is
            used to ensure that the different devices, potentially with different clock
            rates, can work in a synchronized way when performing multiple executions of
            the schedule.
        hw_mapping:
            The hardware configuration dictionary for this specific device. This is one
            of the inner dictionaries of the overall hardware config.
        """
        super().__init__(name, total_play_time, hw_mapping)

        self.portclock_map = self._generate_portclock_to_seq_map()
        self.sequencers = self._construct_sequencers()
        self._settings = PulsarSettings.extract_settings_from_mapping(hw_mapping)

    @property
    @abstractmethod
    def SEQ_TYPE(self):
        pass

    @property
    @abstractmethod
    def MAX_SEQUENCERS(self):
        pass

    def assign_modulation_frequency(self, portclock: Tuple[str, str], freq: float):
        """
        Sets the modulation frequency for a certain portclock belonging to this
        instrument.

        Parameters
        ----------
        portclock:
            A tuple with the port as first element and clock as second.
        freq:
            The modulation frequency to assign to the portclock.

        Returns
        -------

        """
        seq_name = self.portclock_map[portclock]
        seq = self.sequencers[seq_name]
        seq.assign_frequency(freq)

    def _generate_portclock_to_seq_map(self) -> Dict[Tuple[str, str], str]:
        """
        Generates a mapping from portclock tuples to sequencer names.

        Returns
        -------
        :
            A dictionary with as key a portclock tuple and as value the name of a
            sequencer.

        Raises
        ------
        NotImplementedError
            When the hardware mapping contains a dictionary, which is assumed to
            correspond to an output channel, that does not have a name defined in
            self.OUTPUT_TO_SEQ.keys(). Likely this will occur when attempting to use
            real outputs (instead of complex), or when the hardware mapping is invalid.
        """
        output_to_seq = self.OUTPUT_TO_SEQ

        mapping = dict()
        for io, data in self.hw_mapping.items():
            if not isinstance(data, dict):
                continue

            port_clocks = find_all_port_clock_combinations(data)
            if len(port_clocks) > 1:
                raise NotImplementedError(
                    f"{len(port_clocks)} port and clock "
                    f"combinations specified for output {io} "
                    f"(sequencer {output_to_seq[io]}). Multiple "
                    f"sequencers per output is not yet supported "
                    f"by this backend."
                )

            if len(port_clocks) > 0:
                port_clock = port_clocks[0]
                try:
                    mapping[port_clock] = f"seq{output_to_seq[io]}"
                except KeyError as e:
                    raise NotImplementedError(
                        f"Attempting to use non-supported output {io}. "
                        f"Supported output types: "
                        f"{(str(t) for t in output_to_seq.keys())}"
                    ) from e
        return mapping

    def _construct_sequencers(self) -> Dict[str, Pulsar_sequencer_base]:
        """
        Constructs `Pulsar_sequencer_base` objects for each port and clock combination
        belonging to this device.

        Returns
        -------
        :
            A dictionary containing the sequencer objects, the keys correspond to the
            names of the sequencers.
        """
        sequencers = dict()
        for io, io_cfg in self.hw_mapping.items():
            if not isinstance(io_cfg, dict):
                continue

            portclock_dicts = find_inner_dicts_containing_key(io_cfg, "port")
            if len(portclock_dicts) > 1:
                raise NotImplementedError(
                    f"{len(portclock_dicts)} port and clock "
                    f"combinations specified for output {io}. Multiple "
                    f"sequencers per output is not yet supported "
                    f"by this backend."
                )
            portclock_dict = portclock_dicts[0]
            portclock = portclock_dict["port"], portclock_dict["clock"]
            freq = (
                None
                if "interm_freq" in portclock_dict
                else portclock_dict["interm_freq"]
            )

            seq_name = f"seq{self.OUTPUT_TO_SEQ[io]}"
            sequencers[seq_name] = self.SEQ_TYPE(self, seq_name, portclock, freq)
            if "mixer_corrections" in io_cfg:
                sequencers[seq_name].mixer_corrections = MixerCorrections.from_dict(
                    io_cfg["mixer_corrections"]
                )

        if len(sequencers.keys()) > self.MAX_SEQUENCERS:
            raise ValueError(
                f"Attempting to construct too many sequencer compilers. "
                f"Maximum allowed for {self.__class__} is {self.MAX_SEQUENCERS}!"
            )

        return sequencers

    def _distribute_data(self):
        """
        Distributes the pulses and acquisitions assigned to this pulsar over the
        different sequencers based on their portclocks.

        Returns
        -------

        """
        for portclock, pulse_data_list in self._pulses.items():
            for seq in self.sequencers.values():
                if seq.portclock == portclock:
                    seq.pulses = pulse_data_list

        for portclock, acq_data_list in self._acquisitions.items():
            for seq in self.sequencers.values():
                if seq.portclock == portclock:
                    seq.acquisitions = acq_data_list

    def compile(self, repetitions: int = 1) -> Optional[Dict[str, Any]]:
        """
        Performs the actual compilation steps for this pulsar, by calling the sequencer
        level compilation functions and combining them into a single dictionary. The
        compiled program has a settings key, and keys for every sequencer.

        Parameters
        ----------
        repetitions:
            Number of times execution the schedule is repeated

        Returns
        -------
        :
            The compiled program corresponding to this pulsar. It contains an entry for
            every sequencer and general "settings". If the device is not actually used,
            and an empty program is compiled, None is returned instead.
        """
        self._distribute_data()
        program = dict()
        for seq_name, seq in self.sequencers.items():
            seq_program = seq.compile(repetitions=repetitions)
            if seq_program is not None:
                program[seq_name] = seq_program

        if len(program) == 0:
            return None

        self._settings.hardware_averages = repetitions
        program["settings"] = self._settings.to_dict()
        return program


class Pulsar_QCM(Pulsar_base):
    """
    Pulsar QCM specific implementation of the pulsar compiler.

    Attributes
    ----------
    SEQ_TYPE:
        Defines the type of sequencer that this pulsar uses.
    MAX_SEQUENCERS:
        Maximum amount of sequencers that this pulsar implements.
    """

    SEQ_TYPE = QCM_sequencer
    MAX_SEQUENCERS = 2

    def _distribute_data(self):
        """
        Distributes the pulses and acquisitions assigned to this pulsar over the
        different sequencers based on their portclocks. Overrides the function of the
        same name in the superclass to raise an exception in case it attempts to
        distribute acquisitions, since this is not supported by the pulsar QCM.

        Returns
        -------

        Raises
        ------
        RuntimeError
            Pulsar_QCM._acquisitions is not empty
        """
        if self._acquisitions:
            raise RuntimeError(
                f"Attempting to add acquisitions to {self.__class__} {self.name}, "
                f"which is not supported by hardware."
            )
        super()._distribute_data()

    def add_acquisition(self, port: str, clock: str, acq_info: OpInfo):
        """
        Raises an exception when called since the pulsar QCM does not support
        acquisitions.

        Parameters
        ----------
        port:
            The port the pulse needs to be sent to.
        clock:
            The clock for modulation of the pulse. Can be a BasebandClock.
        acq_info:
            Data structure containing all the information regarding this specific
            acquisition operation.

        Returns
        -------

        Raises
        ------
        RuntimeError
            Always
        """
        raise RuntimeError(
            f"Pulsar QCM {self.name} does not support acquisitions. "
            f"Attempting to add acquisition {repr(acq_info)} "
            f"on port {port} with clock {clock}."
        )


class Pulsar_QRM(Pulsar_base):
    """
    Pulsar QRM specific implementation of the pulsar compiler.

    Attributes
    ----------
    SEQ_TYPE:
        Defines the type of sequencer that this pulsar uses.
    MAX_SEQUENCERS:
        Maximum amount of sequencers that this pulsar implements.
    """

    SEQ_TYPE = QRM_sequencer
    MAX_SEQUENCERS = 1


# ---------- Compilation methods ----------
def _assign_frequencies(
    device_compilers: Dict[str, InstrumentCompiler],
    lo_compilers: Dict[str, LocalOscillator],
    hw_mapping: Dict[str, Any],
    portclock_mapping: Dict[Tuple[str, str], str],
    schedule_resources: Dict[str, Any],
):
    """
    Determines the IF or LO frequency based on the clock frequency and assigns it to
    the `InstrumentCompiler`. If the IF is specified the LO frequency is calculated
    based on the constraint `clock_freq = interm_freq + lo_freq`, and vice versa.

    Function removes LOs from lo_compilers if not used by any devices.

    Parameters
    ----------
    device_compilers:
        A dictionary containing all the `InstrumentCompiler` objects for which IQ
        modulation is used. The keys correspond to the QCoDeS names of the instruments.
    lo_compilers:
        A dictionary containing all the `LocalOscillator` objects that are used. The
        keys correspond to the QCoDeS names of the instruments.
    hw_mapping:
        The hardware mapping dictionary describing the whole setup.
    portclock_mapping:
        A dictionary that maps tuples containing a port and a clock to names of
        instruments. The port and clock combinations are unique, but multiple portclocks
        can point to the same instrument.
    schedule_resources:
        The schedule resources containing all the clocks.

    Returns
    -------

    """
    lo_info_dicts = find_inner_dicts_containing_key(hw_mapping, "lo_name")
    los_used = set()
    for lo_info_dict in lo_info_dicts:
        lo_obj = lo_compilers[lo_info_dict["lo_name"]]
        associated_portclock_dicts = find_inner_dicts_containing_key(
            lo_info_dict, "port"
        )

        lo_freq = None
        if "lo_freq" in lo_info_dict:
            lo_freq = lo_info_dict["lo_freq"]
        if lo_freq is None:
            for portclock_dict in associated_portclock_dicts:
                port, clock = portclock_dict["port"], portclock_dict["clock"]
                interm_freq = portclock_dict["interm_freq"]
                if clock in schedule_resources:
                    cl_freq = schedule_resources[clock]["freq"]

                    dev_name = portclock_mapping[(port, clock)]
                    if (port, clock) in device_compilers[dev_name].portclocks_with_data:
                        los_used.add(lo_obj.name)
                    assign_frequency = getattr(
                        device_compilers[dev_name], "assign_modulation_frequency"
                    )
                    # FIXME getattr should probably be removed in favour of inheritance.
                    #  Though this would require an additional layer in structure
                    assign_frequency((port, clock), interm_freq)
                    lo_obj.assign_frequency(cl_freq - interm_freq)
        else:  # lo_freq given
            lo_obj.assign_frequency(lo_freq)
            for portclock_dict in associated_portclock_dicts:
                port, clock = portclock_dict["port"], portclock_dict["clock"]
                dev_name = portclock_mapping[(port, clock)]
                if (port, clock) in device_compilers[dev_name].portclocks_with_data:
                    los_used.add(lo_obj.name)
                assign_frequency = getattr(
                    device_compilers[dev_name], "assign_modulation_frequency"
                )
                if clock in schedule_resources:
                    cl_freq = schedule_resources[clock]["freq"]
                    assign_frequency((port, clock), cl_freq - lo_freq)

    unused_los = set(lo_compilers.keys()).difference(los_used)
    for lo_name in unused_los:
        lo_compilers.pop(lo_name)


def _assign_pulse_and_acq_info_to_devices(
    schedule: Schedule,
    device_compilers: Dict[str, Any],
    portclock_mapping: Dict[Tuple[str, str], str],
):
    """
    Traverses the schedule and generates `OpInfo` objects for every pulse and
    acquisition, and assigns it to the correct `InstrumentCompiler`.

    Parameters
    ----------
    schedule:
        The schedule to extract the pulse and acquisition info from.
    device_compilers:
        Dictionary containing InstrumentCompilers as values and their names as keys.
    portclock_mapping:
        A dictionary that maps tuples containing a port and a clock to names of
        instruments. The port and clock combinations are unique, but multiple portclocks
        can point to the same instrument.

    Returns
    -------

    Raises
    ------
    RuntimeError
        This exception is raised then the function encountered an operation that has no
        pulse or acquisition info assigned to it.
    """
    # for op_hash, op_data in schedule.operations.items():
    for op_timing_constraint in schedule.timing_constraints:
        op_hash = op_timing_constraint["operation_hash"]
        op_data = schedule.operations[op_hash]
        if not op_data.valid_pulse and not op_data.valid_acquisition:
            raise RuntimeError(
                f"Operation {op_hash} is not a valid pulse or acquisition. Please check"
                f" whether the device compilation been performed successfully. "
                f"Operation data: {repr(op_data)}"
            )

        operation_start_time = op_timing_constraint["abs_time"]
        for pulse_data in op_data.data["pulse_info"]:
            if "t0" in pulse_data:
                pulse_start_time = operation_start_time + pulse_data["t0"]
            else:
                pulse_start_time = operation_start_time

            port = pulse_data["port"]
            clock = pulse_data["clock"]
            if port is None:
                continue  # ignore idle pulse

            uuid = make_hash(without(pulse_data, "t0"))
            combined_data = OpInfo(data=pulse_data, timing=pulse_start_time, uuid=uuid)

            dev = portclock_mapping[(port, clock)]
            device_compilers[dev].add_pulse(port, clock, pulse_info=combined_data)

        for acq_data in op_data.data["acquisition_info"]:
            if "t0" in acq_data:
                acq_start_time = operation_start_time + acq_data["t0"]
            else:
                acq_start_time = operation_start_time
            port = acq_data["port"]
            clock = acq_data["clock"]
            if port is None:
                continue

            hashed_dict = without(acq_data, ["t0", "waveforms"])
            hashed_dict["waveforms"] = list()
            for acq in acq_data["waveforms"]:
                hashed_dict["waveforms"].append(without(acq, ["t0"]))
            uuid = make_hash(hashed_dict)

            combined_data = OpInfo(data=acq_data, timing=acq_start_time, uuid=uuid)
            dev = portclock_mapping[(port, clock)]
            device_compilers[dev].add_acquisition(port, clock, acq_info=combined_data)


def _construct_compiler_objects(
    total_play_time: float,
    mapping: Dict[str, Any],
) -> Dict[str, InstrumentCompiler]:
    """
    Traverses the hardware mapping dictionary and instantiates the appropriate
    instrument compiler objects for all the devices that make up the setup. Local
    oscillators are excluded from this step due to them being defined implicitly in the
    hardware mapping.

    Parameters
    ----------
    total_play_time:
        Total time that it takes to execute a single repetition of the schedule with the
        current hardware setup as defined in the mapping.
    mapping:
        The hardware mapping dictionary.

    Returns
    -------
    :
        A dictionary with an `InstrumentCompiler` as value and the QCoDeS name of the
        instrument the compiler compiles for as key.
    """
    device_compilers = dict()
    for device, dev_cfg in mapping.items():
        if not isinstance(dev_cfg, dict):
            continue
        device_type = dev_cfg["type"]

        device_compiler: Callable = getattr(sys.modules[__name__], device_type)
        device_compilers[device] = device_compiler(
            device,
            total_play_time,
            mapping[device],
        )
    return device_compilers


def hardware_compile(
    schedule: Schedule, hardware_map: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Main function driving the compilation. The principle behind the overall compilation
    works as follows:

    For every instrument in the hardware mapping, we instantiate a compiler object. Then
    we assign all the pulses/acquisitions that need to be played by that instrument to
    the compiler, which then compiles for each instrument individually.

    This function then returns all the compiled programs bundled together in a
    dictionary with the QCoDeS name of the instrument as key.

    Parameters
    ----------
    schedule:
        The schedule to compile. It is assumed the pulse and acquisition info is
        already added to the operation. Otherwise and exception is raised.
    hardware_map:
        The hardware mapping of the setup.

    Returns
    -------
    :
        The compiled program
    """
    total_play_time = get_total_duration(schedule)

    portclock_map = generate_port_clock_to_device_map(hardware_map)

    device_compilers = _construct_compiler_objects(
        total_play_time=total_play_time,
        mapping=hardware_map,
    )
    _assign_pulse_and_acq_info_to_devices(
        schedule=schedule,
        device_compilers=device_compilers,
        portclock_mapping=portclock_map,
    )

    lo_compilers = generate_ext_local_oscillators(total_play_time, hardware_map)
    _assign_frequencies(
        device_compilers,
        lo_compilers,
        hw_mapping=hardware_map,
        portclock_mapping=portclock_map,
        schedule_resources=schedule.resources,
    )
    device_compilers.update(lo_compilers)

    compiled_schedule = dict()
    for name, compiler in device_compilers.items():
        compiled_dev_program = compiler.compile(repetitions=schedule.repetitions)

        if compiled_dev_program is not None:
            compiled_schedule[name] = compiled_dev_program

    return compiled_schedule
