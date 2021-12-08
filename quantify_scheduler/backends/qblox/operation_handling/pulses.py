# Repository: https://gitlab.com/quantify-os/quantify-scheduler
# Licensed according to the LICENCE file on the master branch
"""Classes for handling pulses."""

from __future__ import annotations

from typing import Optional, Dict, Any

import logging
import numpy as np

from quantify_scheduler.helpers.waveforms import normalize_waveform_data

from quantify_scheduler.backends.qblox.operation_handling.base import IOperationStrategy

from quantify_scheduler.backends.types import qblox as types
from quantify_scheduler.backends.qblox.qasm_program import QASMProgram
from quantify_scheduler.backends.qblox import helpers, constants, q1asm_instructions


logger = logging.getLogger(__name__)


class PulseStrategyPartial(IOperationStrategy):
    """Contains the logic shared between all the pulses."""

    def __init__(self, operation_info: types.OpInfo, output_mode: str):
        """
        Constructor.

        Parameters
        ----------
        operation_info
            The operation info that corresponds to this pulse.
        output_mode
            Either "real", "imag" or complex depending on whether the signal affects
            only path0, path1 or both.
        """
        self._pulse_info: types.OpInfo = operation_info
        self.output_mode = output_mode

    @property
    def operation_info(self) -> types.OpInfo:
        """Property for retrieving the operation info."""
        return self._pulse_info


class GenericPulseStrategy(PulseStrategyPartial):
    """
    Default class for handling pulses. No assumptions are made with regards to the
    pulse shape and no optimizations are done.
    """

    def __init__(self, operation_info: types.OpInfo, output_mode: str):
        """
        Constructor for this strategy.

        Parameters
        ----------
        operation_info
            The operation info that corresponds to this pulse.
        output_mode
            Either "real", "imag" or complex depending on whether the signal affects
            only path0, path1 or both.
        """
        super().__init__(operation_info, output_mode)

        self.amplitude_path0: Optional[float] = None
        self.amplitude_path1: Optional[float] = None

        self.waveform_index0: Optional[int] = None
        self.waveform_index1: Optional[int] = None

        self.waveform_len: Optional[int] = None

    def generate_data(self, wf_dict: Dict[str, Any]):
        """
        Generates the data and adds them to the wf_dict (if not already present).

        If output_mode == "imag", this moves the real-valued data to be produced on
        path1 instead of path0.

        Parameters
        ----------
        wf_dict
            The dictionary to add the waveform to. N.B. the dictionary is modified in
            function.

        Raises
        ------
        ValueError
            Data is complex (has an imaginary component), but the output_mode is not set
            to "complex".
        """
        op_info = self.operation_info
        waveform_data = helpers.generate_waveform_data(
            op_info.data, sampling_rate=constants.SAMPLING_RATE
        )
        waveform_data, amp_real, amp_imag = normalize_waveform_data(waveform_data)
        self.waveform_len = len(waveform_data)

        _, _, idx_real = helpers.add_to_wf_dict_if_unique(wf_dict, waveform_data.real)
        _, _, idx_imag = helpers.add_to_wf_dict_if_unique(wf_dict, waveform_data.imag)

        if np.any(np.iscomplex(waveform_data)) and not self.output_mode == "complex":
            raise ValueError(
                f"Complex valued {str(op_info)} detected but the sequencer"
                f" is not expecting complex input. This can be caused by "
                f"attempting to play complex valued waveforms on an output"
                f" marked as real.\n\nException caused by {repr(op_info)}."
            )

        if self.output_mode == "imag":
            self.waveform_index0, self.waveform_index1 = idx_real, idx_imag
            self.amplitude_path0, self.amplitude_path1 = amp_imag, amp_real
        else:
            self.waveform_index0, self.waveform_index1 = idx_real, idx_imag
            self.amplitude_path0, self.amplitude_path1 = amp_real, amp_imag

    def insert_qasm(self, qasm_program: QASMProgram):
        """
        Add the assembly instructions for the Q1 sequence processor that corresponds to
        this pulse.

        Parameters
        ----------
        qasm_program
            The QASMProgram to add the assembly instructions to.
        """
        qasm_program.set_gain_from_amplitude(
            self.amplitude_path0, self.amplitude_path1, self.operation_info
        )
        qasm_program.emit(
            q1asm_instructions.PLAY,
            self.waveform_index0,
            self.waveform_index1,
            constants.GRID_TIME,  # N.B. the waveform keeps playing
            comment=f"play {self.operation_info.name} ({self.waveform_len} ns)",
        )
        qasm_program.elapsed_time += constants.GRID_TIME


class StitchedSquarePulseStrategy(PulseStrategyPartial):
    """
    If this strategy is used, a (long) square pulse is generated by stitching shorter
    square pulses together.
    """

    def __init__(self, operation_info: types.OpInfo, output_mode: str):
        """
        Constructor for StitchedSquarePulseStrategy.

        Parameters
        ----------
        operation_info
            The operation info that corresponds to this pulse.
        output_mode
            Either "real", "imag" or complex depending on whether the signal affects
            only path0, path1 or both.
        """
        super().__init__(operation_info, output_mode)

        self.amplitude_path0: Optional[float] = None
        self.amplitude_path1: Optional[float] = None

        self.waveform_index0: Optional[int] = None
        self.waveform_index1: Optional[int] = None

    def generate_data(self, wf_dict: Dict[str, Any]):
        """
        Produces the waveform data for the stitched square pulse. This will be of a
        fixed duration.

        If the output mode is set to "complex", path1 will play all zeros. Otherwise
        both paths will play ones, but the amplitude will be set to 0 on one of them.

        Parameters
        ----------
        wf_dict
            The dictionary to add the waveform to. N.B. the dictionary is modified in
            function.
        """
        op_info = self.operation_info
        amplitude = op_info.data["amp"]

        array_with_ones = np.ones(
            int(constants.PULSE_STITCHING_DURATION * constants.SAMPLING_RATE)
        )
        _, _, idx_ones = helpers.add_to_wf_dict_if_unique(wf_dict, array_with_ones.real)
        if self.output_mode == "complex":
            _, _, idx_zeros = helpers.add_to_wf_dict_if_unique(
                wf_dict, array_with_ones.imag
            )
            self.waveform_index0, self.waveform_index1 = idx_ones, idx_zeros
            self.amplitude_path0, self.amplitude_path1 = amplitude, 0
        else:
            self.waveform_index0, self.waveform_index1 = idx_ones, idx_ones

            if self.output_mode == "imag":
                self.amplitude_path0, self.amplitude_path1 = 0, amplitude
            else:
                self.amplitude_path0, self.amplitude_path1 = amplitude, 0

    def insert_qasm(self, qasm_program: QASMProgram):
        """

        Parameters
        ----------
        qasm_program

        Returns
        -------

        """
        duration = self.operation_info.duration
        repetitions = int(duration // constants.PULSE_STITCHING_DURATION)

        qasm_program.set_gain_from_amplitude(
            self.amplitude_path0, self.amplitude_path1, self.operation_info
        )
        if repetitions > 1:
            with qasm_program.loop(
                label=f"stitch{len(qasm_program.instructions)}",
                repetitions=repetitions,
            ):
                qasm_program.emit(
                    q1asm_instructions.PLAY,
                    self.waveform_index0,
                    self.waveform_index1,
                    helpers.to_grid_time(constants.PULSE_STITCHING_DURATION),
                )
                qasm_program.elapsed_time += repetitions * helpers.to_grid_time(
                    constants.PULSE_STITCHING_DURATION
                )
        elif repetitions == 1:
            qasm_program.emit(
                q1asm_instructions.PLAY,
                self.waveform_index0,
                self.waveform_index1,
                helpers.to_grid_time(constants.PULSE_STITCHING_DURATION),
            )
            qasm_program.elapsed_time += helpers.to_grid_time(
                constants.PULSE_STITCHING_DURATION
            )

        pulse_time_remaining = helpers.to_grid_time(
            duration % constants.PULSE_STITCHING_DURATION
        )
        if pulse_time_remaining > 0:
            logger.warning(
                f"Using pulse stitching with pulse duration that is not a multiple of "
                f"{constants.PULSE_STITCHING_DURATION} s. This can cause unexpected "
                f"behavior to occur.\n\n{repr(self.operation_info)}"
            )
            qasm_program.emit(
                q1asm_instructions.PLAY,
                self.waveform_index0,
                self.waveform_index1,
                pulse_time_remaining,
            )
            qasm_program.emit(
                q1asm_instructions.SET_AWG_GAIN,
                0,
                0,
                comment="set to 0 at end of pulse",
            )
        qasm_program.elapsed_time += pulse_time_remaining


class StaircasePulseStrategy(PulseStrategyPartial):
    def __init__(self, operation_info: types.OpInfo, output_mode: str):
        super().__init__(operation_info, output_mode)

    def generate_data(self, wf_dict: Dict[str, Any]):
        return None

    def insert_qasm(self, qasm_program: QASMProgram):
        pulse = self.operation_info
        num_steps = pulse.data["num_steps"]
        start_amp = pulse.data["start_amp"]
        final_amp = pulse.data["final_amp"]
        step_duration_ns = helpers.to_grid_time(pulse.duration / num_steps)

        offset_param_label = (
            "offset_awg_path1" if self.output_mode == "imag" else "offset_awg_path0"
        )

        amp_step = (final_amp - start_amp) / (num_steps - 1)
        amp_step_immediate = qasm_program.expand_from_normalised_range(
            amp_step / qasm_program.static_hw_properties.max_awg_output_voltage,
            constants.IMMEDIATE_SZ_OFFSET,
            offset_param_label,
            pulse,
        )
        start_amp_immediate = qasm_program.expand_from_normalised_range(
            start_amp / qasm_program.static_hw_properties.max_awg_output_voltage,
            constants.IMMEDIATE_SZ_OFFSET,
            offset_param_label,
            pulse,
        )
        if start_amp_immediate < 0:
            start_amp_immediate += constants.REGISTER_SIZE  # registers are unsigned

        self._generate_staircase_loop(
            qasm_program,
            start_amp_immediate,
            amp_step_immediate,
            step_duration_ns,
            num_steps,
        )

    def _generate_staircase_loop(
        self,
        qasm_program: QASMProgram,
        start_amp_immediate: int,
        amp_step_immediate: int,
        step_duration_ns: int,
        num_steps: int,
    ):
        with qasm_program.temp_register(2) as (offs_reg, offs_reg_zero):
            qasm_program.emit(
                q1asm_instructions.SET_AWG_GAIN,
                constants.IMMEDIATE_SZ_GAIN // 2,
                constants.IMMEDIATE_SZ_GAIN // 2,
                comment="set gain to known value",
            )

            # Initialize registers
            qasm_program.emit(
                q1asm_instructions.MOVE,
                start_amp_immediate,
                offs_reg,
                comment="keeps track of the offsets",
            )
            qasm_program.emit(
                q1asm_instructions.MOVE,
                0,
                offs_reg_zero,
                comment="zero for unused output path",
            )

            qasm_program.emit(q1asm_instructions.NEW_LINE)
            with qasm_program.loop(
                f"ramp{len(qasm_program.instructions)}", repetitions=num_steps
            ):
                self._generate_step(
                    qasm_program,
                    offs_reg,
                    offs_reg_zero,
                    amp_step_immediate,
                )
                qasm_program.auto_wait(step_duration_ns - constants.GRID_TIME)

            qasm_program.elapsed_time += (
                step_duration_ns * (num_steps - 1) if num_steps > 1 else 0
            )

            qasm_program.emit(
                q1asm_instructions.SET_AWG_OFFSET,
                0,
                0,
                comment="return offset to 0 after staircase",
            )
            qasm_program.emit(q1asm_instructions.NEW_LINE)

    def _generate_step(
        self,
        qasm_program: QASMProgram,
        offs_reg: str,
        offs_reg_zero: str,
        amp_step_immediate: int,
    ):
        if self.output_mode == "imag":
            qasm_program.emit(
                q1asm_instructions.SET_AWG_OFFSET, offs_reg_zero, offs_reg
            )
        else:
            qasm_program.emit(
                q1asm_instructions.SET_AWG_OFFSET, offs_reg, offs_reg_zero
            )
        qasm_program.emit(
            q1asm_instructions.UPDATE_PARAMETERS,
            constants.GRID_TIME,
        )
        qasm_program.elapsed_time += constants.GRID_TIME
        if amp_step_immediate >= 0:
            qasm_program.emit(
                q1asm_instructions.ADD,
                offs_reg,
                amp_step_immediate,
                offs_reg,
                comment=f"next incr offs by {amp_step_immediate}",
            )
        else:
            qasm_program.emit(
                q1asm_instructions.SUB,
                offs_reg,
                abs(amp_step_immediate),
                offs_reg,
                comment=f"next decr offs by {abs(amp_step_immediate)}",
            )
