# Repository: https://gitlab.com/quantify-os/quantify-scheduler
# Licensed according to the LICENCE file on the master branch
"""Compiler classes for Qblox backend."""
from __future__ import annotations

from typing import Optional, Dict, Any

from quantify_scheduler.backends.qblox import compiler_container
from quantify_scheduler.backends.qblox.compiler_abc import (
    InstrumentCompiler,
    ControlDeviceCompiler,
    PulsarBase,
    PulsarBaseband,
    PulsarRF,
)
from quantify_scheduler.backends.types.qblox import LOSettings
from quantify_scheduler.backends.qblox.constants import (
    NUMBER_OF_SEQUENCERS_QCM,
    NUMBER_OF_SEQUENCERS_QRM,
)


class LocalOscillator(InstrumentCompiler):
    """
    Implementation of an `InstrumentCompiler` that compiles for a generic LO. The main
    difference between this class and the other compiler classes is that it doesn't take
    pulses and acquisitions.
    """

    def __init__(
        self,
        parent: compiler_container.CompilerContainer,
        name: str,
        total_play_time: float,
        hw_mapping: Dict[str, Any],
    ):
        """
        Constructor for a local oscillator compiler.

        Parameters
        ----------
        parent
            Reference to the parent container object.
        name
            QCoDeS name of the device it compiles for.
        total_play_time
            Total time execution of the schedule should go on for. This parameter is
            used to ensure that the different devices, potentially with different clock
            rates, can work in a synchronized way when performing multiple executions of
            the schedule.
        hw_mapping
            The hardware mapping dict for this instrument.
        """
        super().__init__(parent, name, total_play_time, hw_mapping)
        self._settings = LOSettings.from_mapping(hw_mapping)

    @property
    def frequency(self) -> float:
        """
        Getter for the frequency.

        Returns
        -------
        :
            The current frequency.
        """
        return self._settings.lo_freq

    @frequency.setter
    def frequency(self, value: float):
        """
        Sets the lo frequency for this device if no frequency is specified, but raises
        an exception otherwise.

        Parameters
        ----------
        value
            The frequency to set it to.

        Raises
        -------
        ValueError
            Occurs when a frequency has been previously set and attempting to set the
            frequency to a different value than what it is currently set to. This would
            indicate an invalid configuration in the hardware mapping.
        """
        if self._settings.lo_freq is not None:
            if value != self._settings.lo_freq:
                raise ValueError(
                    f"Attempting to set LO {self.name} to frequency {value}, "
                    f"while it has previously already been set to "
                    f"{self._settings.lo_freq}!"
                )
        self._settings.lo_freq = value

    def compile(self, repetitions: int = 1) -> Optional[Dict[str, Any]]:
        """
        Compiles the program for the LO InstrumentCoordinator component.

        Parameters
        ----------
        repetitions
            Number of times execution the schedule is repeated.

        Returns
        -------
        :
            Dictionary containing all the information the InstrumentCoordinator
            component needs to set the parameters appropriately.
        """
        if self.frequency is None:
            return None
        return self._settings.to_dict()


# ---------- pulsar sequencer classes ----------

# pylint: disable=invalid-name
class Pulsar_QCM(PulsarBaseband):
    """
    Pulsar QCM specific implementation of the pulsar compiler.
    """

    _max_sequencers: int = NUMBER_OF_SEQUENCERS_QCM
    """Maximum number of sequencers available in the instrument."""
    awg_output_volt: float = 2.5
    """Peak output voltage of the AWG"""
    marker_configuration: dict = {"start": 1, "end": 0}
    """Marker values to activate/deactivate the O1 marker"""
    supports_acquisition: bool = False
    """Specifies whether the device can perform acquisitions."""


# pylint: disable=invalid-name
class Pulsar_QRM(PulsarBaseband):
    """
    Pulsar QRM specific implementation of the pulsar compiler.
    """

    _max_sequencers: int = NUMBER_OF_SEQUENCERS_QRM
    """Maximum number of sequencers available in the instrument."""
    awg_output_volt: float = 0.5
    """Peak output voltage of the AWG"""
    marker_configuration: dict = {"start": 1, "end": 0}
    """Marker values to activate/deactivate the I1 marker"""
    supports_acquisition: bool = True
    """Specifies whether the device can perform acquisitions."""


class Pulsar_QCM_RF(PulsarRF):
    """
    Pulsar QCM-RF specific implementation of the pulsar compiler.
    """

    _max_sequencers: int = NUMBER_OF_SEQUENCERS_QCM
    """Maximum number of sequencer available in the instrument."""
    awg_output_volt: float = 0.25
    """Peak output voltage of the AWG"""
    marker_configuration: dict = {"start": 6, "end": 8}
    """
    Marker values to activate/deactivate the O1 marker,
    and the output switches for O1/O2
    """
    supports_acquisition: bool = False
    """Specifies whether the device can perform acquisitions."""


class Pulsar_QRM_RF(PulsarRF):
    """
    Pulsar QRM-RF specific implementation of the pulsar compiler.
    """

    _max_sequencers: int = NUMBER_OF_SEQUENCERS_QRM
    """Maximum number of sequencer available in the instrument."""
    awg_output_volt: float = 0.25
    """Peak output voltage of the AWG"""
    marker_configuration: dict = {"start": 1, "end": 4}
    """
    Marker values to activate/deactivate the I1 marker,
    and the output switch for O1
    """
    supports_acquisition: bool = True
    """Specifies whether the device can perform acquisitions."""


class Cluster(ControlDeviceCompiler):
    """
    Compiler class for a Qblox cluster.
    """

    compiler_classes: Dict[str, type] = {
        "Pulsar_QCM": Pulsar_QCM,
        "Pulsar_QRM": Pulsar_QRM,
    }
    """References to the individual module compiler classes."""
    supports_acquisition: bool = True
    """Specifies that the Cluster supports performing acquisitions."""

    def __init__(
        self,
        parent: compiler_container.CompilerContainer,
        name: str,
        total_play_time: float,
        hw_mapping: Dict[str, Any],
    ):
        super().__init__(
            parent=parent,
            name=name,
            total_play_time=total_play_time,
            hw_mapping=hw_mapping,
        )
        self.instrument_compilers: dict = self.construct_instrument_compilers()

    def construct_instrument_compilers(self) -> Dict[str, PulsarBase]:
        instrument_compilers = dict()
        for name, cfg in self.hw_mapping.items():
            if not isinstance(cfg, dict):
                continue  # not an instrument definition
            if "instrument_type" not in cfg:
                raise KeyError(
                    f"Module {name} of cluster {self.name} is specified in "
                    f"the config, but does not specify an instrument_type."
                )
            instrument_type: str = cfg["instrument_type"]
            if instrument_type not in self.compiler_classes:
                raise KeyError(
                    f"Specified unknown instrument_type {instrument_type} as"
                    f" a module for cluster {self.name}. Please select one "
                    f"of: {self.compiler_classes.keys()}."
                )
            compiler_type: type = self.compiler_classes[instrument_type]
            instance = compiler_type(
                None, name=name, total_play_time=self.total_play_time, hw_mapping=cfg
            )
            instrument_compilers[name] = instance
        return instrument_compilers

    def prepare(self) -> None:
        self.distribute_data()
        # Need to somehow make this work with ext los, for now we skip this.
        for compiler in self.instrument_compilers.values():
            compiler.distribute_data()

    def distribute_data(self) -> None:
        for compiler in self.instrument_compilers.values():
            for portclock in compiler.portclocks:
                if portclock in self._pulses:
                    compiler.add_pulse(self._pulses[portclock])
                if portclock in self._acquisitions:
                    compiler.add_acquisition(self._acquisitions[portclock])

    def compile(self, repetitions: int = 1) -> Optional[Dict[str, Any]]:
        program = dict()
        program["settings"] = {"reference_source": self.hw_mapping["ref"]}
        for compiler in self.instrument_compilers.values():
            instrument_program = compiler.compile(repetitions)
            if instrument_program is not None and len(instrument_program) > 0:
                program[compiler.name] = instrument_program
        if len(program) == 0:
            program = None
        return program
