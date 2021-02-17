# -----------------------------------------------------------------------------
# Description:    Compiler backend for the Pulsar QCM.
# Repository:     https://gitlab.com/quantify-os/quantify-scheduler
# Copyright (C) Qblox BV & Orange Quantum Systems Holding BV (2020-2021)
# -----------------------------------------------------------------------------
import os
import inspect
import json
from typing import Optional
from collections import namedtuple
from qcodes.utils.helpers import NumpyJSONEncoder
from columnar import columnar
from columnar.exceptions import TableOverflowError
from qcodes import Instrument
import numpy as np
from quantify.scheduler.types import Operation
from quantify.scheduler.resources import Resource
from quantify.scheduler.waveforms import modulate_wave
from quantify.data.handling import gen_tuid, create_exp_folder
from quantify.utilities.general import make_hash, without, import_func_from_string


PulsarModulations = namedtuple(
    "PulsarModulations",
    ["gain_I", "gain_Q", "offset", "phase", "phase_delta"],
    defaults=[None, None, None, None, None],
)

QCM_DRIVER_VER = "0.2.2"
QRM_DRIVER_VER = "0.2.2"


class QCM_sequencer(Resource):
    """
    A single sequencer unit contained in a Pulsar_QCM module.

    For pulse-sequencing purposes, the Pulsar_QCM_sequencer can be considered
    a channel capable of outputting complex valued signals (I, and Q).
    """

    def __init__(
        self,
        name: str,
        port: str,
        clock: str,
        lo_freq: float = None,
        interm_freq: float = 0,
        phase: float = 0,
    ):
        """
        A QCM sequencer.

        Parameters
        -------------
        name : str
            The name of this resource.
        port: str
            Port where the pulses generated by the sequencer should be applied.
        clock: str
            Clock that is tracked by the NCO.
        interm_freq: float
            Frequency used for intermodulation.
        nco_phase: float
            Phase of NCO.
        """
        super().__init__()

        self._timing_tuples = []
        self._pulse_dict = {}

        self.data = {
            "name": name,
            "type": str(self.__class__.__name__),
            "port": port,
            "clock": clock,
            "interm_freq": interm_freq,
            "lo_freq": lo_freq,
            "phase": phase,
            "sampling_rate": 1e9,
        }

    @property
    def timing_tuples(self) -> list:
        """
        A list of timing tuples.
        """
        return self._timing_tuples

    @property
    def pulse_dict(self) -> dict:
        return self._pulse_dict


class QRM_sequencer(Resource):
    def __init__(
        self,
        name: str,
        port: str,
        clock: str,
        lo_freq: float = None,
        interm_freq: float = 0,
        phase: float = 0,
    ):
        """
        A QRM sequencer.

        Parameters
        -------------
        name : str
            the name of this resource.
        port: str
            Port where the pulses generated by the sequencer should be applied.
        clock: str
            Clock that is tracked by the NCO.
        nco_freq: float
            Frequency of NCO.
        nco_phase: float
            Phase of NCO.
        """
        super().__init__()

        self._timing_tuples = []
        self._pulse_dict = {}

        self.data = {
            "name": name,
            "type": str(self.__class__.__name__),
            "port": port,
            "clock": clock,
            "lo_freq": lo_freq,
            "interm_freq": interm_freq,
            "phase": phase,
            "sampling_rate": 1e9,
        }

    @property
    def timing_tuples(self):
        """
        A list of timing tuples con
        """
        return self._timing_tuples

    @property
    def pulse_dict(self):
        return self._pulse_dict


class Q1ASMBuilder:
    """
    Generates a q1asm program instruction by instruction. Handles splitting overflowing operation times.
    """

    IMMEDIATE_SZ = pow(2, 16) - 1
    CYCLE_TIME_ns = 4
    AWG_OUTPUT_VOLT_QCM = 2.5
    AWG_OUTPUT_VOLT_QRM = 0.5

    # phase counter
    AWG_ACQ_SMPL_PATH_WITH = 4
    SYS_CLK_FREQ_MHZ = 250
    PH_INCR_MAX = AWG_ACQ_SMPL_PATH_WITH * SYS_CLK_FREQ_MHZ * 1000000
    NCO_LUT_DEPTH = 400
    PH_INCR_COARSE_FACT = PH_INCR_MAX / NCO_LUT_DEPTH
    PH_INCR_FINE_FACT = PH_INCR_COARSE_FACT / NCO_LUT_DEPTH

    def __init__(self):
        self.rows = []

    def get_str(self):
        """
        Returns
        -------
        str
            The program
        """
        try:
            return columnar(self.rows, no_borders=True)
        # running in a sphinx environment can trigger a TableOverFlowError
        except TableOverflowError:
            return columnar(self.rows, no_borders=True, terminal_width=120)

    @staticmethod
    def _iff(label):
        return f"{label}:" if label else ""

    def _split_playtime(self, duration):
        split = []
        while duration > self.IMMEDIATE_SZ:
            split.append(self.IMMEDIATE_SZ)
            duration -= self.IMMEDIATE_SZ
        split.append(duration)
        return split

    def _check_playtime(self, duration):
        if duration < self.CYCLE_TIME_ns:
            raise ValueError(
                f"duration {duration}ns < cycle time {self.CYCLE_TIME_ns}ns"
            )
        return duration

    def _calculate_phase_params(self, degrees):
        phase = int((degrees / 360) * self.PH_INCR_MAX)
        static_ph_coarse = int(phase / self.PH_INCR_COARSE_FACT)
        phase_corase = static_ph_coarse * self.PH_INCR_COARSE_FACT
        static_ph_fine = int((phase - phase_corase) / self.PH_INCR_FINE_FACT)
        phase_fine = static_ph_fine * self.PH_INCR_FINE_FACT
        static_ph_ufine = int(phase - phase_corase - phase_fine)
        return static_ph_coarse, static_ph_fine, static_ph_ufine

    def _expand_from_normalised_range(self, val, param):
        if val < -1.0 or val > 1.0:
            raise ValueError(
                f"{param} parameter of PulsarModulations must be in the range 0.0:1.0"
            )
        return int(val * self.IMMEDIATE_SZ / 2)

    def update_parameters(
        self, modulations: PulsarModulations, device, pulsar_type: str
    ):
        if not modulations:
            return
        if modulations.gain_I is not None:
            if device == "awg":
                if pulsar_type == "QCM_sequencer":
                    awg_output_volt = self.AWG_OUTPUT_VOLT_QCM
                elif pulsar_type == "QRM_sequencer":
                    awg_output_volt = self.AWG_OUTPUT_VOLT_QRM
                else:
                    raise ValueError(f"Device {pulsar_type} not supported.")
            else:
                awg_output_volt = 1.0
            normalised = modulations.gain_I / awg_output_volt
            gain_I_val = self._expand_from_normalised_range(normalised, "Gain")
            gain_Q_val = gain_I_val
            if modulations.gain_Q is not None:
                normalised = modulations.gain_Q / awg_output_volt
                gain_Q_val = self._expand_from_normalised_range(normalised, "Gain")
            self.rows.append(
                ["", f"set_{device}_gain", f"{gain_I_val},{gain_Q_val}", "#Set gain"]
            )
        if modulations.offset is not None:
            offset_val = self._expand_from_normalised_range(
                modulations.offset, "Offset"
            )
            self.rows.append(
                ["", f"set_{device}_offs", f"{offset_val},{offset_val}", ""]
            )
        if modulations.phase is not None:
            coarse, fine, ufine = self._calculate_phase_params(modulations.phase)
            # switched 'set_ph_delta' and 'delta' to workaround bug in firmware. Should be reverted in new release.
            self.rows.append(["", "set_ph_delta", f"{coarse},{fine},{ufine}", ""])
        if modulations.phase_delta is not None:
            coarse, fine, ufine = self._calculate_phase_params(modulations.phase_delta)
            self.rows.append(["", "set_ph", f"{coarse},{fine},{ufine}", ""])

    def line_break(self):
        self.rows.append(["", "", "", ""])

    def wait_sync(self, label):
        self.rows.append([self._iff(label), "wait_sync", "4", "#sync"])

    def move(self, label, source, target, comment):
        self.rows.append([self._iff(label), "move", f"{source},{target}", comment])

    def play(self, label, I_idx, Q_idx, playtime, comment):
        for duration in self._split_playtime(playtime):
            args = f"{I_idx},{Q_idx},{int(duration)}"
            row = [self._iff(label), "play", args, comment]
            label = None
            self.rows.append(row)

    def acquire(self, label, I_idx, Q_idx, playtime, comment):
        for duration in self._split_playtime(playtime):
            args = f"{I_idx},{Q_idx},{int(duration)}"
            row = [self._iff(label), "acquire", args, comment]
            label = None
            self.rows.append(row)

    def set_mrk(self, label, val):
        self.rows.append([self._iff(label), "set_mrk", val, ""])

    def wait(self, label, playtime, comment):
        for duration in self._split_playtime(playtime):
            row = [
                str(label or ""),
                "wait",
                int(self._check_playtime(duration)),
                comment,
            ]
            label = None
            self.rows.append(row)

    def jmp(self, label, target, comment):
        self.rows.append([self._iff(label), "jmp", f"@{target}", comment])

    def stop(self, label, comment):
        self.rows.append([self._iff(label), "stop", "", comment])

    def forloop(self, label, start, reg, comment):
        self.rows.append([self._iff(label), "loop", f"{reg},@{start}", comment])


# todo this doesnt work for custom waveform functions - use visitors?
def _prepare_pulse(description, gain=0.0):
    def dummy_load_params(param_list):
        for param, default in param_list:
            description[param] = default
        return description

    def calc_amplitude_correction(amp, line_gain_db):
        return amp / 10 ** (
            line_gain_db / 20
        )  # correct for line losses (line_gain_db is in dB)

    wf_func = description["wf_func"]
    if (
        wf_func == "quantify.scheduler.waveforms.square"
        or wf_func == "quantify.scheduler.waveforms.soft_square"
    ):
        params = PulsarModulations(
            gain_I=calc_amplitude_correction(description["amp"], gain),
            gain_Q=calc_amplitude_correction(description["amp"], gain),
        )
        return params, dummy_load_params([("amp", 1.0)])
    if wf_func == "quantify.scheduler.waveforms.ramp":
        params = PulsarModulations(
            gain_I=calc_amplitude_correction(description["amp"], gain),
            gain_Q=calc_amplitude_correction(description["amp"], gain),
        )
        return params, dummy_load_params([("amp", 1.0)])
    elif wf_func == "quantify.scheduler.waveforms.drag":
        params = PulsarModulations(
            gain_I=calc_amplitude_correction(description["G_amp"], gain),
            gain_Q=calc_amplitude_correction(description["G_amp"], gain),
            phase=description["phase"],
        )
        return params, dummy_load_params(
            [
                ("G_amp", 1.0),
                ("D_amp", description["D_amp"] / description["G_amp"]),
                ("phase", 0),
            ]
        )
    elif wf_func is None:
        return None, description
    else:
        raise ValueError(f"Unknown wave {wf_func}")


def _extract_device_output_sequencer(hw_mapping_inverted: dict, port: str, clock: str):
    """
    Extracts the device, output and sequencer for a port&clock pair

    Parameters
    ----------
    hw_mapping_inverted : dict
        The inverted hardware mapping (call _invert_hardware_mapping to generate this)
    port : str
        A qubit port identity
    clock : str
        A transition clock identity

    Returns
    -------
    str
        Device name
    str
        Output channel name
    str
        Sequencer name
    """
    portclock = _portclock(port, clock)
    if portclock not in hw_mapping_inverted:
        raise ValueError(
            f"No device found for the combination of port '{port}' and clock '{clock}'"
        )
    return hw_mapping_inverted[portclock]


def _extract_nco_en(
    hardware_mapping: dict, hw_mapping_inverted: dict, port: str, clock: str
):
    """
    Extracts whether or not modulation with the nco is enabled in the hardware_mapping. Is key is absent,
    default to False.

    Returns
        bool
            nco_en, is the nco modulation enabled
    """
    qcm, output, seq = _extract_device_output_sequencer(
        hw_mapping_inverted, port, clock
    )
    seq_mapping = hardware_mapping[qcm][output][seq]
    if "nco_en" in seq_mapping:
        return seq_mapping["nco_en"]
    else:
        return False


def _extract_interm_freq(
    hardware_mapping: dict,
    hw_mapping_inverted: dict,
    port: str,
    clock: str,
    clock_freq: float,
):
    """
    Determines the lo and nco frequencies based on the targetted clock frequency and the hardware mapping.

    In the mapping file it is possible to specify either the LO frequency or the IF frequency.
    The clock determines the target or RF frequency.

    The following relation should hold
        LO + IF = RF

    Returns
    -------
    float
        LO, frequency of the local oscillator
    float
        IF, inter-modulation frequency used to modulate the signal
    float
        RF, the frequency of the signal
    """
    qcm, output, seq = _extract_device_output_sequencer(
        hw_mapping_inverted, port, clock
    )
    lo_freq = hardware_mapping[qcm][output]["lo_freq"]
    interm_freq = hardware_mapping[qcm][output][seq]["interm_freq"]

    if lo_freq is None and interm_freq is None:
        raise ValueError(
            "frequency under constrained, specify either the lo_freq or nco_freq in the hardware mapping"
        )
    elif lo_freq is None and interm_freq is not None:
        # LO = RF - IF
        lo_freq = clock_freq - interm_freq
    elif interm_freq is None and lo_freq is not None:
        # RF - LO = IF
        interm_freq = clock_freq - lo_freq
    elif lo_freq is not None and interm_freq is not None:
        raise ValueError(
            "frequency over constrained, do not specify both "
            "the lo_freq and nco_freq in the hardware mapping."
        )

    return lo_freq, interm_freq


def _extract_io(
    hardware_mapping: dict, hw_mapping_inverted: dict, port: str, clock: str
):
    """
    Extracts the name of an output channel for the given port&clock pair

    Parameters
    ----------
    hardware_mapping : dict
        The hardware mapping
    hw_mapping_inverted : dict
        The inverted hardware mapping (call _invert_hardware_mapping to generate this)
    port : str
        A qubit port identity
    clock : str
        A transition clock identity

    Returns
    -------
    str
        The name of the output channel
    """
    _, output, _ = _extract_device_output_sequencer(hw_mapping_inverted, port, clock)
    return output


def _extract_pulsar_config(
    hardware_mapping: dict, hw_mapping_inverted: dict, port: str, clock: str
):
    """
    Extracts the configuration of a pulsar device for the given port&clock pair

    Parameters
    ----------
    hardware_mapping : dict
        The hardware mapping
    hw_mapping_inverted : dict
        The inverted hardware mapping (call _invert_hardware_mapping to generate this)
    port : str
        A qubit port identity
    clock : str
        A transition clock identity

    Returns
    -------
    dict
        The configuration dict of the device
    """
    qcm, _, _ = _extract_device_output_sequencer(hw_mapping_inverted, port, clock)
    return hardware_mapping[qcm]


def _extract_pulsar_type(
    hardware_mapping: dict, hw_mapping_inverted: dict, port: str, clock: str
):
    """
    Extracts the type of a pulsar device for the given port&clock pair

    Parameters
    ----------
    hardware_mapping : dict
        The hardware mapping
    hw_mapping_inverted : dict
        The inverted hardware mapping (call _invert_hardware_mapping to generate this)
    port : str
        A qubit port identity
    clock : str
        A transition clock identity

    Returns
    -------
    str
        The type of the pulsar
    """
    return _extract_pulsar_config(hardware_mapping, hw_mapping_inverted, port, clock)[
        "type"
    ]


def _extract_gain(
    hardware_mapping: dict, hw_mapping_inverted: dict, port: str, clock: str
):
    """
    Extracts the gain of an output channel for the given port&clock pair

    Parameters
    ----------
    hardware_mapping : dict
        The hardware mapping
    hw_mapping_inverted : dict
        The inverted hardware mapping (call _invert_hardware_mapping to generate this)
    port : str
        A qubit port identity
    clock : str
        A transition clock identity

    Returns
    -------
    double
        The gain of the output channel
    """
    qcm, output, _ = _extract_device_output_sequencer(hw_mapping_inverted, port, clock)
    return hardware_mapping[qcm][output]["line_gain_db"]


def _portclock(port: str, clock: str) -> Optional[str]:
    """
    Creates the unique ID of port and clock in a fixed format
    """
    if port is None or clock is None:
        return None
    return f"{port}_{clock}"


def _invert_hardware_mapping(hardware_mapping):
    """
    Inverts the hardware mapping to create a fast lookup structure for port-clock identity to pulsar
    """
    portclock_reference = {}
    for device_name, device_cfg in hardware_mapping.items():
        if not isinstance(device_cfg, dict):
            continue
        if device_cfg["mode"] == "complex":
            if device_cfg["type"] == "Pulsar_QCM":
                io = ["complex_output_0", "complex_output_1"]
            elif device_cfg["type"] == "Pulsar_QRM":
                io = ["complex_output_0"]
        elif device_cfg["mode"] == "real":
            if device_cfg["type"] == "Pulsar_QCM":
                io = ["real_output_0", "real_output_1", "real_output_", "real_output_3"]
            elif device_cfg["type"] == "Pulsar_QRM":
                raise NotImplementedError("QRM in real mode is not yet implemented")
        else:
            raise ValueError("Unrecognised output mode")
        for io in io:
            for seq_name, seq_cfg in device_cfg[io].items():
                if not isinstance(seq_cfg, dict):
                    continue
                portclock = _portclock(seq_cfg["port"], seq_cfg["clock"])
                if not portclock:  # undefined port/clock
                    continue
                if portclock in portclock_reference:
                    raise ValueError(
                        f"Duplicate port and clock combination: '{seq_cfg['port']}' and '{seq_cfg['clock']}'"
                    )
                portclock_reference[portclock] = (device_name, io, seq_name)
    return portclock_reference


def pulsar_assembler_backend(
    schedule,
    mapping,
    tuid=None,
    debug=False,
    iterations=1,
):
    """
    Create sequencer configuration files for multiple Qblox pulsar modules.

    Sequencer configuration files contain assembly, a waveform dictionary and the
    parameters to be configured for every pulsar sequencer.

    The sequencer configuration files are stored in the quantify datadir
    (see :func:`~quantify.data.handling.get_datadir`)


    Parameters
    ------------
    schedule : :class:`~quantify.scheduler.types.Schedule` :
        The schedule to convert into assembly.
    mapping : :class:`~quantify.scheduler.types.mapping` :
        The mapping that describes how the Pulsars are connected to the ports.
    tuid : :class:`~quantify.data.types.TUID` :
        a tuid of the experiment the schedule belongs to. If set to None, a new TUID will be generated to store
        the sequencer configuration files.
    debug : bool
        if True will produce extra debug output
    iterations : int
        number of times to perform this program

    Returns
    ----------
    schedule : :class:`~quantify.scheduler.types.Schedule` :
        The schedule
    config_dict : dict
        of sequencer names as keys with json filenames as values
    """

    max_seq_duration = 0
    acquisitions = set()
    portclock_mapping = _invert_hardware_mapping(mapping)

    for pls_idx, t_constr in enumerate(schedule.timing_constraints):
        op = schedule.operations[t_constr["operation_hash"]]

        if len(op["pulse_info"]) == 0:
            # this exception is raised when no pulses have been added yet.
            raise ValueError(f"Operation {op.name} has no pulse info")

        for p_ref in op["pulse_info"] + op["acquisition_info"]:
            if "abs_time" not in t_constr:
                raise ValueError(
                    f"Absolute timing has not been determined for the schedule '{schedule.name}'"
                )

            # copy to avoid changing the reference operation in the master schedule list
            p = p_ref.copy()

            port = p["port"]
            clock_id = p["clock"]

            if port is None:
                continue  # pulses with None port will be ignored by this backend
                # this is used to add for example the reset and idle pulses

            gain = _extract_gain(
                hardware_mapping=mapping,
                hw_mapping_inverted=portclock_mapping,
                port=port,
                clock=clock_id,
            )
            params, p = _prepare_pulse(p, gain)

            t0 = t_constr["abs_time"] + p["t0"]
            pulse_id = make_hash(without(p, ["t0"]))

            if "acq_index" in p:
                acquisitions.add(pulse_id)
                if p["acq_index"] > 0:
                    raise NotImplementedError("Binning in QRM is not yet implemented")

            # the combination of port + clock id is a unique combination that is associated to a sequencer
            portclock = _portclock(port, clock_id)
            interm_freq = 0
            lo_freq = 0
            if portclock not in schedule.resources.keys():
                pulsar_type = _extract_pulsar_type(
                    mapping, portclock_mapping, port, clock_id
                )
                if pulsar_type == "Pulsar_QCM":
                    sequencer_t = QCM_sequencer
                elif pulsar_type == "Pulsar_QRM":
                    sequencer_t = QRM_sequencer
                else:
                    raise ValueError(f"Unrecognized Pulsar type '{pulsar_type}'")

                lo_freq, interm_freq = _extract_interm_freq(
                    hardware_mapping=mapping,
                    hw_mapping_inverted=portclock_mapping,
                    port=port,
                    clock=clock_id,
                    clock_freq=schedule.resources[clock_id]["freq"],
                )
                schedule.add_resources(
                    [
                        sequencer_t(
                            portclock,
                            port=port,
                            clock=clock_id,
                            interm_freq=interm_freq,
                            lo_freq=lo_freq,
                        )
                    ]
                )

            seq = schedule.resources[portclock]
            seq.timing_tuples.append(
                (round(t0 * seq["sampling_rate"]), pulse_id, params)
            )

            # determine waveform
            if pulse_id not in seq.pulse_dict.keys():
                # the pulsar backend makes use of real-time pulse modulation
                t = np.arange(0, 0 + p["duration"], 1 / seq["sampling_rate"])
                wf_func = import_func_from_string(p["wf_func"])

                # select the arguments for the waveform function that are present in pulse info
                par_map = inspect.signature(wf_func).parameters
                wf_kwargs = {}
                for kw in par_map.keys():
                    if kw in p.keys():
                        wf_kwargs[kw] = p[kw]
                # Calculate the numerical waveform using the wf_func
                wf = wf_func(t=t, **wf_kwargs)
                nco_en = _extract_nco_en(
                    hardware_mapping=mapping,
                    hw_mapping_inverted=portclock_mapping,
                    port=port,
                    clock=clock_id,
                )
                if nco_en:
                    raise NotImplementedError(
                        "Modulation using the nco is not yet implemented in pulsar_backend"
                    )
                else:
                    wf = modulate_wave(t, wf, interm_freq)
                    # TODO add mixer corrections
                seq.pulse_dict[pulse_id] = wf

            # FIXME, this is used to synchronise loop length, but will be removed in favour of wait_sync when phase
            # FIXME, reset is implemented in hardware, QCM #56
            seq_duration = seq.timing_tuples[-1][0] + len(seq.pulse_dict[pulse_id])
            max_seq_duration = (
                max_seq_duration if max_seq_duration > seq_duration else seq_duration
            )
            max_seq_duration = max_seq_duration + 4 - (max_seq_duration % 4)

    # Creating the files
    if tuid is None:
        tuid = gen_tuid()
    # Should use the folder of the matching file if tuid already exists
    exp_folder = create_exp_folder(tuid=tuid, name=schedule.name + "_schedule")
    seq_folder = os.path.join(exp_folder, "schedule")
    os.makedirs(seq_folder, exist_ok=True)

    # Convert timing tuples and pulse dicts for each sequencer into assembly configs
    config_dict = {}
    # the config_dict is a dict with resource names as keys and sequencer filenames as values.
    for resource in schedule.resources.values():
        # only selects the resource objects here that are valid sequencer units.
        if hasattr(resource, "timing_tuples"):
            seq_cfg = generate_sequencer_cfg(
                pulse_info=resource.pulse_dict,
                timing_tuples=sorted(resource.timing_tuples),
                sequence_duration=max_seq_duration,
                acquisitions=acquisitions,
                iterations=iterations,
                pulsar_type=resource.data["type"],
            )
            seq_cfg["instr_cfg"] = resource.data

            if debug:
                qasm_dump = os.path.join(seq_folder, f"{resource.name}_sequencer.q1asm")
                with open(qasm_dump, "w") as f:
                    f.write(seq_cfg["program"])

            seq_fn = os.path.join(seq_folder, f"{resource.name}_sequencer_cfg.json")
            with open(seq_fn, "w") as f:
                json.dump(seq_cfg, f, cls=NumpyJSONEncoder, indent=4)

            dev, _, seq = _extract_device_output_sequencer(
                portclock_mapping, resource["port"], resource["clock"]
            )
            if dev not in config_dict.keys():
                config_dict[dev] = {}
            config_dict[dev][seq] = seq_fn

    return schedule, config_dict


def _check_driver_version(instr, ver):
    driver_vers = instr.get_idn()["build"]["driver"]["version"]
    if driver_vers != ver:
        device = instr.get_idn()["device"]
        raise ValueError(
            f"Backend requires {device} to have driver version {ver}, found {driver_vers} installed."
        )


def configure_pulsars(config: dict, mapping: dict, hw_mapping_inverted: dict = None):
    """
    Configures multiple pulsar modules based on a configuration dictionary.

    Parameters
    ------------
    config : dict
        Dictionary with resource_names as keys and filenames of sequencer config json files as values.
    mapping : dict
        Hardware mapping dictionary
    """

    pulsars = {}
    if not hw_mapping_inverted:
        hw_mapping_inverted = _invert_hardware_mapping(mapping)

    # the keys in the config are ignored. The files are assumed to be self consistent.
    for pulsar_config in config.values():
        for config_fn in pulsar_config.values():
            with open(config_fn) as seq_config:
                data = json.load(seq_config)
                instr_cfg = data["instr_cfg"]  # all info is in the config
                pulsar_dict = _extract_pulsar_config(
                    hardware_mapping=mapping,
                    hw_mapping_inverted=hw_mapping_inverted,
                    port=instr_cfg["port"],
                    clock=instr_cfg["clock"],
                )

                io = _extract_io(
                    hardware_mapping=mapping,
                    hw_mapping_inverted=hw_mapping_inverted,
                    port=instr_cfg["port"],
                    clock=instr_cfg["clock"],
                )

                # configure settings
                if io == "complex_output_0":
                    seq_idx = 0
                elif io == "complex_output_1":
                    seq_idx = 1
                else:
                    # real outputs are not yet supported
                    raise ValueError(f"Output {io} not supported.")

                if pulsar_dict["name"] not in pulsars:
                    try:
                        pulsar = Instrument.find_instrument(pulsar_dict["name"])
                    except KeyError as e:
                        raise KeyError(f'Could not find instrument "{str(e)}"')

                    pulsars[pulsar_dict["name"]] = pulsar

                    # todo, remove check after QCM #57 is closed
                    if pulsar_dict["ref"] == "int":
                        if pulsar.reference_source() != "internal":
                            pulsar.reference_source("internal")
                    elif pulsar_dict["ref"] == "ext":
                        if pulsar.reference_source() != "external":
                            pulsar.reference_source("external")
                    else:
                        raise ValueError(
                            f"Unrecognized reference setting {pulsar_dict['ref']}"
                        )
                else:
                    pulsar = pulsars[pulsar_dict["name"]]

                is_qrm = instr_cfg["type"] == "QRM_sequencer"
                if is_qrm:
                    _check_driver_version(pulsar, QRM_DRIVER_VER)
                else:
                    _check_driver_version(pulsar, QCM_DRIVER_VER)

                pulsar.set(f"sequencer{seq_idx}_sync_en", True)
                # FIXME, re-add hardware modulation when hardware demodulation is supported
                # pulsar.set('sequencer{}_nco_freq'.format(seq_idx), instr_cfg['nco_freq'])
                # pulsar.set('sequencer{}_nco_phase_offs'.format(seq_idx), instr_cfg['nco_phase'])
                # mod_enable = True if instr_cfg['nco_freq'] != 0 or instr_cfg['nco_phase'] != 0 else False
                # pulsar.set('sequencer{}_mod_en_awg'.format(seq_idx), mod_enable)
                for path in (0, 1):
                    awg_path = f"_awg_path{path}"
                    pulsar.set(f"sequencer{seq_idx}_cont_mode_en{awg_path}", False)
                    pulsar.set(
                        f"sequencer{seq_idx}_cont_mode_waveform_idx{awg_path}", 0
                    )
                    pulsar.set(f"sequencer{seq_idx}_upsample_rate{awg_path}", 0)
                    pulsar.set(f"sequencer{seq_idx}_gain{awg_path}", 1)
                    pulsar.set(f"sequencer{seq_idx}_offset{awg_path}", 0)

                if is_qrm:
                    pulsar.set(
                        f"sequencer{seq_idx}_trigger_mode_acq_path0", "sequencer"
                    )
                    pulsar.set(
                        f"sequencer{seq_idx}_trigger_mode_acq_path1", "sequencer"
                    )

                pulsar.set(f"sequencer{seq_idx}_waveforms_and_program", config_fn)

            # TODO set LO_frequency


def build_waveform_dict(pulse_info: dict, acquisitions: set) -> dict:
    """
    Allocates numerical pulse representation to indices and formats for sequencer JSON.

    Parameters
    ----------
    pulse_info : dict
        Pulse ID to array-like numerical representation
    acquisitions : set
        Set of pulse_IDs which are acquisitions

    Returns
    -------
        Dictionary mapping pulses to numerical representation and memory index
    """
    sequencer_cfg = {"waveforms": {"awg": {}, "acq": {}}}
    for pulse_id, data in pulse_info.items():
        arr = np.array(data)
        I = arr.real  # noqa: E741
        Q = arr.imag  # real-valued arrays automatically evaluate to an array of zeros
        device = "awg" if pulse_id not in acquisitions else "acq"
        sequencer_cfg["waveforms"][device][f"{pulse_id}_I"] = {
            "data": I,
            "index": len(sequencer_cfg["waveforms"][device]),
        }
        sequencer_cfg["waveforms"][device][f"{pulse_id}_Q"] = {
            "data": Q,
            "index": len(sequencer_cfg["waveforms"][device]),
        }
    return sequencer_cfg


# todo this needs a serious clean up
def build_q1asm(
    timing_tuples: list,
    pulse_dict: dict,
    sequence_duration: int,
    acquisitions: set,
    iterations: int,
    pulsar_type: str,
) -> str:
    """
    N.B. as of 2021-01-21 NOT READY FOR PARALLEL OPERATIONS IN THE SCHEDULE

    Converts operations and waveforms to a q1asm program. This function verifies these hardware based constraints:

        * Each pulse must run for at least the INSTRUCTION_CLOCK_TIME
        * Each operation must have a timing separation of at least INSTRUCTION_CLOCK_TIME

    .. warning:
        The above restrictions apply to any generated WAIT instructions.

    Parameters
    ----------
    timing_tuples : list
        A sorted list of tuples matching timings to pulse_IDs.
    pulse_dict : dict
        pulse_IDs to numerical waveforms with registered index in waveform memory.
    sequence_duration : int
        maximum runtime of this sequence
    acquisitions : set
        pulse_IDs which are acquisitions
    iterations : int
        number of times to run this program
    pulsar_type: str
        either 'QCM_sequencer' or 'QRM_sequencer' to know which device properties to use

    Returns
    -------
        A q1asm program in a string.
    """

    def get_pulse_runtime(pulse_id):
        device = "awg" if pulse_id not in acquisitions else "acq"
        return len(pulse_dict[device][f"{pulse_id}_I"]["data"])

    def get_pulse_finish_time(pulse_idx):
        start_time = timing_tuples[pulse_idx][0]
        runtime = get_pulse_runtime(timing_tuples[pulse_idx][1])
        return start_time + runtime

    # Checks if our automatically generated 'sync' waits are too short.
    def auto_wait(label, duration, comment, previous):
        try:
            if duration > 0:
                q1asm.wait(label, duration, comment)
        except ValueError as e:
            raise ValueError(
                f"Generated wait for '{previous[0]}':'{previous[1]}' caused exception '{str(e)}'"
            )

    q1asm = Q1ASMBuilder()
    q1asm.move("", iterations, "R0", "")
    q1asm.wait_sync("start")
    q1asm.set_mrk("", 1)

    if timing_tuples and get_pulse_finish_time(-1) > sequence_duration:
        raise ValueError(
            f"Provided sequence_duration '{sequence_duration}' is less than "
            + f"the total runtime of this sequence ({get_pulse_finish_time(-1)})."
        )
    clock = 0  # current execution time
    for idx, (timing, pulse_id, hardware_modulations) in enumerate(timing_tuples):
        # check if we must wait before beginning our next section
        wait_duration = timing - clock
        device = "awg" if pulse_id not in acquisitions else "acq"
        auto_wait(
            "", wait_duration, "#Wait", None if idx == 0 else timing_tuples[idx - 1]
        )
        q1asm.line_break()

        q1asm.update_parameters(hardware_modulations, device, pulsar_type)

        I = pulse_dict[device][f"{pulse_id}_I"]["index"]  # noqa: E741
        Q = pulse_dict[device][f"{pulse_id}_Q"]["index"]

        # duration should be the pulse length or next start time
        next_timing = (
            timing_tuples[idx + 1][0] - timing_tuples[idx][0]
            if idx < len(timing_tuples) - 1
            else np.Inf
        )
        # duration in nanoseconds, QCM sample rate is # 1Gsps
        pulse_runtime = get_pulse_runtime(pulse_id)
        duration = min(next_timing, pulse_runtime)

        if device == "awg":
            q1asm.play("", I, Q, duration, "")
        else:
            q1asm.acquire("", I, Q, duration, "")

        clock += duration + wait_duration

    # check if we must wait to sync up with fellow sequencers
    final_wait_duration = sequence_duration - clock
    if timing_tuples:
        auto_wait(
            "", final_wait_duration, "#Sync with other sequencers", timing_tuples[-1]
        )

    q1asm.line_break()
    q1asm.forloop("", "start", "R0", "")
    q1asm.stop("", "")
    return q1asm.get_str()


def generate_sequencer_cfg(
    pulse_info,
    timing_tuples,
    sequence_duration: int,
    acquisitions: set,
    iterations: int,
    pulsar_type: str,
):
    """
    Generate a JSON compatible dictionary for defining a sequencer configuration. Contains a list of waveforms and a
    program in a q1asm string.

    Parameters
    ----------
    timing_tuples : list
        A sorted list of tuples matching timings to pulse_IDs.
    pulse_info : dict
        pulse_IDs to numerical waveforms with registered index in waveform memory.
    sequence_duration : int
        maximum runtime of this sequence
    acquisitions : set
        pulse_IDs which are acquisitions
    iterations : int
        number of times to run this program
    pulsar_type: str
        either 'QCM_sequencer' or 'QRM_sequencer' to know which device properties to use

    Returns
    -------
        Sequencer configuration
    """
    cfg = build_waveform_dict(pulse_info, acquisitions)
    cfg["program"] = build_q1asm(
        timing_tuples,
        cfg["waveforms"],
        sequence_duration,
        acquisitions,
        iterations,
        pulsar_type,
    )
    return cfg
