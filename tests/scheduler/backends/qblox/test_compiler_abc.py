# Repository: https://gitlab.com/quantify-os/quantify-scheduler
# Licensed according to the LICENCE file on the main branch
"""Tests for the InstrumentCompiler subclasses."""
from itertools import permutations
from typing import List
from unittest.mock import Mock

import pytest
from quantify_scheduler.backends.qblox import q1asm_instructions
from quantify_scheduler.backends.qblox.instrument_compilers import QrmRfModule
from quantify_scheduler.backends.types.qblox import OpInfo


DEFAULT_PORT = "q0:res"
DEFAULT_CLOCK = "q0.ro"


def pulse_with_waveform(
    timing: float, duration: float = 1e-7, port: str = DEFAULT_PORT, clock=DEFAULT_CLOCK
) -> OpInfo:
    """Create an OpInfo obect that is recognized as a non-idle pulse."""
    return OpInfo(
        name="ExamplePulse",
        data={
            "wf_func": "something",
            "port": port,
            "clock": clock,
            "duration": duration,
        },
        timing=timing,
    )


def virtual_pulse(
    timing: float, duration: float = 0.0, port: str = DEFAULT_PORT, clock=DEFAULT_CLOCK
) -> OpInfo:
    """Create an OpInfo obect that is recognized as a virtual pulse."""
    return OpInfo(
        name="ResetClockPhase",
        data={
            "wf_func": None,
            "port": port,
            "clock": clock,
            "duration": duration,
            "reset_clock_phase": True,
        },
        timing=timing,
    )


def offset_instruction(
    timing: float, duration: float = 1e-7, port: str = DEFAULT_PORT, clock=DEFAULT_CLOCK
) -> OpInfo:
    """Create an OpInfo object that is recognized as an offset instruction."""
    return OpInfo(
        name="VoltageOffset",
        data={
            "instruction": q1asm_instructions.SET_AWG_OFFSET,
            "offset_path_0": 0.5,
            "offset_path_1": 0.0,
            "port": port,
            "clock": clock,
            "duration": duration,
        },
        timing=timing,
    )


def acquisition(
    timing: float, duration: float = 1e-7, port: str = DEFAULT_PORT, clock=DEFAULT_CLOCK
) -> OpInfo:
    """Create an OpInfo obect that is recognized as an acquisition."""
    return OpInfo(
        name="Trace",
        data={
            "waveforms": [],
            "duration": duration,
            "port": port,
            "clock": clock,
            "acq_channel": 0,
            "acq_index": 0,
            "protocol": "Trace",
        },
        timing=timing,
    )


@pytest.mark.parametrize(
    "op_list",
    list(
        permutations(
            [
                pulse_with_waveform(0.0),
                offset_instruction(1e-7),
                offset_instruction(2e-7),
                pulse_with_waveform(2e-7),
                virtual_pulse(1e-7),
            ]
        )
    ),
)
def test_param_update_after_offset_except_if_simultaneous_play(op_list: List[OpInfo]):
    """Test if upd_param is inserted after a VoltageOffset in the correct places."""
    component = QrmRfModule(
        parent=Mock(), name="Test", total_play_time=3e-7, instrument_cfg={}
    )

    for op in op_list:
        component.add_pulse(DEFAULT_PORT, DEFAULT_CLOCK, op)
    component._insert_update_parameters()

    assert len(component._pulses[(DEFAULT_PORT, DEFAULT_CLOCK)]) == 6
    assert (
        OpInfo(
            name="UpdateParameters",
            data={
                "t0": 0,
                "duration": 0.0,
                "instruction": q1asm_instructions.UPDATE_PARAMETERS,
                "port": DEFAULT_PORT,
                "clock": DEFAULT_CLOCK,
            },
            timing=1e-7,
        )
        in component._pulses[(DEFAULT_PORT, DEFAULT_CLOCK)]
    )


@pytest.mark.parametrize(
    "op_list",
    list(
        permutations(
            [
                pulse_with_waveform(0.0),
                offset_instruction(1e-7),
                acquisition(1e-7),
                virtual_pulse(1e-7),
            ]
        )
    )
    + list(
        permutations(
            [
                pulse_with_waveform(0.0),
                offset_instruction(2e-7, duration=0.0),
                acquisition(1e-7),
                virtual_pulse(1e-7),
            ]
        )
    ),
)
def test_no_parameter_update(op_list: List[OpInfo]):
    """Test if no upd_param is inserted where it is not necessary."""
    component = QrmRfModule(
        parent=Mock(), name="Test", total_play_time=2e-7, instrument_cfg={}
    )

    for op in op_list:
        if op.is_acquisition:
            component.add_acquisition(DEFAULT_PORT, DEFAULT_CLOCK, op)
        else:
            component.add_pulse(DEFAULT_PORT, DEFAULT_CLOCK, op)

    component._insert_update_parameters()

    assert len(component._pulses[(DEFAULT_PORT, DEFAULT_CLOCK)]) == 3
    for op in component._pulses[(DEFAULT_PORT, DEFAULT_CLOCK)]:
        assert op.name != "UpdateParameters"
