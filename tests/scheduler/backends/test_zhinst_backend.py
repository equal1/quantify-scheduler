# pylint: disable=missing-module-docstring
# pylint: disable=missing-class-docstring
# pylint: disable=missing-function-docstring

# -----------------------------------------------------------------------------
# Description:    Tests for Zurich Instruments backend.
# Repository:     https://gitlab.com/quantify-os/quantify-scheduler
# Copyright (C) Qblox BV & Orange Quantum Systems Holding BV (2020-2021)
# -----------------------------------------------------------------------------
from __future__ import annotations

import json
import math
from pathlib import Path
from textwrap import dedent
from typing import Any, Dict, List
from unittest.mock import ANY, call

import numpy as np
import pytest
from zhinst.qcodes import hdawg, uhfqa, uhfli, mfli
from zhinst.toolkit.control import drivers

import quantify.scheduler.backends.zhinst_backend as zhinst_backend
import quantify.scheduler.waveforms as waveforms
from quantify.scheduler.backends.types.zhinst import Device, DeviceType
from quantify.scheduler.backends.zhinst_backend import (
    _WaveformDestination,
)
from quantify.scheduler.enums import ModulationModeType
from quantify.scheduler.gate_library import X90, Measure, Reset, X
from quantify.scheduler.helpers.schedule import (
    get_port_timeline,
    get_pulse_info_by_uuid,
)
from quantify.scheduler.helpers.waveforms import (
    GetWaveformPartial,
    get_waveform_by_pulseid,
)
from quantify.scheduler.math import closest_number_ceil
from quantify.scheduler.schedules.acquisition import (
    raw_trace_schedule,
    ssb_integration_complex_schedule,
)
from quantify.scheduler.types import Schedule
from quantify.scheduler.backends.zhinst import helpers as zi_helpers


@pytest.fixture
def uhfqa_hardware_map() -> Dict[str, Any]:
    return json.loads(
        """
        {
          "backend": "quantify.scheduler.backends.zhinst_backend.create_pulsar_backend",
          "devices": [
            {
              "name": "uhfqa0",
              "ref": "ext",
              "channel_0": {
                "port": "q0:res",
                "clock": "q0.ro",
                "mode": "real",
                "modulation": "premod",
                "lo_freq": 4.8e9,
                "interm_freq": -50e6,
                "triggers": [
                  2
                ]
              }
            }
          ]
        }
        """
    )


@pytest.fixture
def hdawg_hardware_map() -> Dict[str, Any]:
    return json.loads(
        """
        {
          "backend": "quantify.scheduler.backends.zhinst_backend.create_pulsar_backend",
          "devices": [
            {
              "name": "hdawg0",
              "ref": "int",
              "channelgrouping": 0,
              "channel_0": {
                "port": "q0:mw",
                "clock": "q0.01",
                "mode": "complex",
                "modulation": "premod",
                "lo_freq": 4.8e9,
                "interm_freq": -50e6,
                "markers": [
                  "AWG_MARKER1",
                  "AWG_MARKER2"
                ]
              },
              "channel_1": {
                "port": "q1:mw",
                "clock": "q1.01",
                "mode": "complex",
                "modulation": "premod",
                "lo_freq": 4.8e9,
                "interm_freq": -50e6,
                "triggers": [
                  1
                ]
              }
            }
          ]
        }
        """
    )


@pytest.fixture
def create_device():
    def _create_device(hardware_map: Dict[str, Any], index: int = 0) -> Device:
        return Device.schema().load(hardware_map["devices"][index])

    yield _create_device


@pytest.fixture
def create_uhfqa_mock(mocker):
    def _create_uhfqa_mock() -> uhfqa.UHFQA:
        features_mock = mocker.Mock()
        features_mock.parameters = {"devtype": mocker.Mock(return_value="UHFQA")}

        mock = mocker.Mock(spec=uhfqa.UHFQA)
        mock.configure_mock(
            **{
                "features": features_mock,
                "name": "hdawg0",
                "_serial": "dev2299",
                "result_source": mocker.Mock(),
            }
        )

        def create_UHFQA_AWG(i: int) -> uhfqa.AWG:
            _sequence_params = {
                "sequence_parameters": {
                    "clock_rate": 1.8e9,  # GSa/s
                }
            }

            def get_string(value: str):
                if value == "directory":
                    return "./"
                return ""

            _module_mock = mocker.Mock()
            _module_mock.get_string = get_string
            _awg_attrs = {
                "_index": i,
                "sequence_params": _sequence_params,
                "_module": _module_mock,
            }

            awg_mock = mocker.Mock(
                spec=uhfqa.AWG,
            )
            awg_mock.configure_mock(
                **{
                    "name": "uhfqa_awg",
                    "index": i,
                    "output1": mocker.Mock(return_value=None),
                    "output2": mocker.Mock(return_value=None),
                    "_awg": mocker.Mock(spec=drivers.uhfqa.AWG, **_awg_attrs),
                }
            )

            return awg_mock

        def create_UHFQA_ReadoutChannel(i: int) -> uhfqa.ReadoutChannel:
            attrs = {
                "_index": i,
                "rotation": mocker.Mock(),
            }
            return mocker.MagicMock(spec=uhfqa.ReadoutChannel, **attrs)

        mock.awg = create_UHFQA_AWG(0)
        mock.channels = list(map(create_UHFQA_ReadoutChannel, range(10)))

        return mock

    yield _create_uhfqa_mock


@pytest.fixture
def create_hdawg_mock(mocker):
    def _create_hdawg_mock(channels: int) -> hdawg.HDAWG:
        awg_count = int(channels / 2)
        features_mock = mocker.Mock()
        features_mock.parameters = {
            "devtype": mocker.Mock(return_value=f"HDAWG{channels}")
        }

        mock = mocker.Mock(
            spec=hdawg.HDAWG,
        )
        mock.configure_mock(
            **{
                "features": features_mock,
                "name": "hdawg0",
                "_serial": "dev8030",
            }
        )

        def create_HDAWG_AWG(i: int):
            # https://www.zhinst.com/sites/default/files/documents/2020-09/ziHDAWG_UserManual_20.07.1.pdf
            # Section: 4.14.3 Constansts and Variables (page 181)
            _sequence_params = {
                "sequence_parameters": {
                    "clock_rate": 2.4e9,  # GSa/s
                }
            }

            def get_string(value: str):
                if value == "directory":
                    return "./"
                return ""

            _module_mock = mocker.Mock()
            _module_mock.get_string = get_string
            _awg_attrs = {
                "_index": i,
                "sequence_params": _sequence_params,
                "_module": _module_mock,
            }

            awg_mock = mocker.Mock(spec=hdawg.AWG)
            awg_mock.configure_mock(
                **{
                    "name": f"hdawg_awg-{i}",
                    "index": i,
                    "output1": mocker.Mock(return_value=None),
                    "output2": mocker.Mock(return_value=None),
                    "_awg": mocker.Mock(spec=drivers.hdawg.AWG, **_awg_attrs),
                    "modulation_freq": mocker.Mock(),
                    "modulation_phase_shift": mocker.Mock(),
                    "gain1": mocker.Mock(),
                    "gain2": mocker.Mock(),
                }
            )
            return awg_mock

        mock.awgs = list(map(create_HDAWG_AWG, range(awg_count)))
        return mock

    yield _create_hdawg_mock


@pytest.mark.parametrize(
    "unsupported_device_type", [(DeviceType.UHFLI), (DeviceType.MFLI)]
)
def test_setup_zhinst_backend_supported_devices(
    mocker, unsupported_device_type: DeviceType, create_schedule_with_pulse_info
):
    # Arrange
    zhinst_hardware_map = json.loads(
        """
        {
          "backend": "quantify.scheduler.backends.zhinst_backend.create_pulsar_backend",
          "devices": [
            {
              "name": "device_name",
              "ref": "none",
              "channel_0": {
                "port": "q0:mw",
                "clock": "q0.ro",
                "mode": "real",
                "modulation": "none",
                "lo_freq": 4.8e9,
                "interm_freq": -50e6
              }
            }
          ]
        }
        """
    )

    if unsupported_device_type == DeviceType.UHFLI:
        instrument = mocker.create_autospec(uhfli.UHFLI, instance=True)
    elif unsupported_device_type == DeviceType.MFLI:
        instrument = mocker.create_autospec(mfli.MFLI, instance=True)

    mocker.patch(
        "qcodes.instrument.base.Instrument.find_instrument",
        return_value=instrument,
    )

    # Act
    with pytest.raises(NotImplementedError) as execinfo:
        zhinst_backend.setup_zhinst_backend(
            create_schedule_with_pulse_info(), zhinst_hardware_map
        )

    # Assert
    assert (
        f"Unable to create zhinst backend for '{unsupported_device_type.value}'!"
        == str(execinfo.value)
    )


def test_setup_zhinst_backend_hdawg4_successfully(
    mocker,
    create_hdawg_mock,
    create_schedule_with_pulse_info,
    hdawg_hardware_map: Dict[str, Any],
) -> None:
    # Arrange
    (q0, q1) = ("q0", "q1")
    schedule = Schedule("test")
    schedule.add(Reset(q0, q1))
    schedule.add(X90(q0))
    schedule.add(X90(q1))
    schedule = create_schedule_with_pulse_info(schedule)

    hdawg_mock: hdawg.HDAWG = create_hdawg_mock(4)
    clock_rate = hdawg_mock.awgs[0]._awg.sequence_params["sequence_parameters"][
        "clock_rate"
    ]
    step = 1 / clock_rate  # (1/sampling_rate)
    pulse_duration = 1.6e-08

    # ceil((stop - start)/step)
    expected_wave_length: int = math.ceil((pulse_duration - 0) / step)
    corrected_wave_length: int = closest_number_ceil(
        expected_wave_length, zhinst_backend.WAVEFORM_GRANULARITY[DeviceType.HDAWG]
    )
    mocker.patch(
        "qcodes.instrument.base.Instrument.find_instrument",
        return_value=hdawg_mock,
    )
    mocker.patch.object(zi_helpers, "set_value")
    modulate_wave_spy = mocker.patch.object(
        waveforms, "modulate_wave", wraps=waveforms.modulate_wave
    )
    set_wave_vector_mock = mocker.patch.object(zi_helpers, "set_wave_vector")
    set_commandtable_data_mock = mocker.patch.object(
        zi_helpers, "set_commandtable_data"
    )
    write_seqc_file_mock = mocker.patch.object(
        zi_helpers, "write_seqc_file", return_value=Path("awg-0.seqc")
    )

    # Act
    zhinst_backend.setup_zhinst_backend(schedule, hdawg_hardware_map)

    # Assert
    modulate_wave_spy.assert_called()
    set_wave_vector_mock.assert_called()
    set_commandtable_data_mock.assert_called()
    write_seqc_file_mock.assert_called()

    expected_call = [call(hdawg_mock, 0, 0, ANY), call(hdawg_mock, 1, 0, ANY)]
    assert set_wave_vector_mock.call_args_list == expected_call
    # Assert waveform sizes
    for call_args in set_wave_vector_mock.call_args_list:
        args, _ = call_args
        waveform_data = args[3]
        assert isinstance(waveform_data, (np.ndarray, np.generic))
        assert len(waveform_data) == (corrected_wave_length * 2)

    expected_call = [call(hdawg_mock, 0, ANY), call(hdawg_mock, 1, ANY)]
    assert set_commandtable_data_mock.call_args_list == expected_call

    expected_call = [call(hdawg_mock, 0, ANY), call(hdawg_mock, 1, ANY)]
    assert set_commandtable_data_mock.call_args_list == expected_call

    expected_call = [
        call(hdawg_mock.awgs[0], ANY, "hdawg_awg-0.seqc"),
        call(hdawg_mock.awgs[1], ANY, "hdawg_awg-1.seqc"),
    ]
    assert write_seqc_file_mock.call_args_list == expected_call


def test_hdawg4_sequence(
    mocker,
    create_hdawg_mock,
    create_schedule_with_pulse_info,
    hdawg_hardware_map: Dict[str, Any],
) -> None:
    # Arrange
    schedule = Schedule("test")
    schedule.add(Reset("q0"))
    schedule.add(X("q0"))
    schedule.add(Measure("q0"))
    schedule = create_schedule_with_pulse_info(schedule)

    hdawg_mock: hdawg.HDAWG = create_hdawg_mock(4)
    mocker.patch(
        "qcodes.instrument.base.Instrument.find_instrument",
        return_value=hdawg_mock,
    )
    mocker.patch.object(zi_helpers, "set_value")
    modulate_wave_spy = mocker.patch.object(
        waveforms, "modulate_wave", wraps=waveforms.modulate_wave
    )
    set_wave_vector_mock = mocker.patch.object(zi_helpers, "set_wave_vector")
    set_commandtable_data_mock = mocker.patch.object(
        zi_helpers, "set_commandtable_data"
    )
    write_seqc_file_mock = mocker.patch.object(
        zi_helpers, "write_seqc_file", return_value=Path("awg-0.seqc")
    )

    expected_seqc = dedent(
        """\
    // Generated by quantify-scheduler.
    // Variables
    var __repetitions__ = 1;
    wave w0 = placeholder(48);\n
    // Operations
    // Schedule offset: 0.0002s 60000 clocks
    // Schedule duration: 4.3600000000000735e-07s 131 clocks
    // Sequence start: 0.0s 0 clocks
    // Sequence duration: 1.6000000000007978e-08s 5 clocks
    // Sequence end: 1.6000000000007978e-08s 5 clocks
    // Dead time: 5e-06s 1500 clocks
    // Line delay: -1s 0 clocks
    assignWaveIndex(w0, w0, 0);
    setTrigger(0);
    repeat(__repetitions__)
    {
      setTrigger(AWG_MARKER1 + AWG_MARKER2);
      executeTableEntry(0);	// clock=0 pulse=0
      waitWave();	// clock=0
      setTrigger(0);	// clock=6
      // Dead time
      wait(1621);	//  n_instr=3
    }
    setTrigger(0);
    """
    ).lstrip("\n")

    # Act
    zhinst_backend.setup_zhinst_backend(schedule, hdawg_hardware_map)

    # Assert
    modulate_wave_spy.assert_called()
    set_wave_vector_mock.assert_called()
    set_commandtable_data_mock.assert_called()
    write_seqc_file_mock.assert_called()
    # Note: Assert inner variable for better error messsage
    args, _ = write_seqc_file_mock.call_args
    assert args[1] == expected_seqc
    write_seqc_file_mock.assert_called_with(
        hdawg_mock.awgs[0], expected_seqc, "hdawg_awg-0.seqc"
    )


@pytest.mark.parametrize(
    "channels,channelgrouping,enabled_channels", [(4, 0, [0, 1]), (4, 1, [0])]
)
def test__program_hdawg4_channelgrouping(
    mocker,
    create_hdawg_mock,
    create_device,
    create_schedule_with_pulse_info,
    hdawg_hardware_map,
    channels: int,
    channelgrouping: int,
    enabled_channels: List[int],
):
    # Arrange
    (q0, q1) = ("q0", "q1")
    schedule = Schedule("test")
    schedule.add(Reset(q0, q1))
    schedule.add(X90(q0))
    schedule.add(X90(q1))
    schedule = create_schedule_with_pulse_info(schedule)

    hdawg_mock: hdawg.HDAWG = create_hdawg_mock(channels)

    device: Device = create_device(hdawg_hardware_map)
    device.type = DeviceType.HDAWG
    device.channelgrouping = channelgrouping

    channels_list = list(range(int(channels / 2)))
    disabled_channels = list(set(channels_list) - set(enabled_channels))

    mocker.patch.object(zhinst_backend, "_program_sequences_hdawg")
    mocker.patch.object(zhinst_backend, "_program_modulation")
    mocker.patch("quantify.scheduler.helpers.waveforms.resize_waveforms")
    mocker.patch.object(zhinst_backend, "_set_waveforms")
    zhinsthelper_set_mock = mocker.patch.object(zi_helpers, "set_value")

    # Act
    zhinst_backend._program_hdawg(
        hdawg_mock,
        device,
        schedule,
        get_pulse_info_by_uuid(schedule),
        get_waveform_by_pulseid(schedule),
        get_port_timeline(schedule),
    )

    # Assert
    zhinsthelper_set_mock.assert_called_with(
        hdawg_mock, "system/awg/channelgrouping", channelgrouping
    )

    expected_output_calls = [call("off"), call("on")]
    for i in enabled_channels:
        assert hdawg_mock.awgs[i].output1.mock_calls == expected_output_calls
        assert hdawg_mock.awgs[i].output2.mock_calls == expected_output_calls

    expected_output_calls = [call("off")]
    for i in disabled_channels:
        assert hdawg_mock.awgs[i].output1.mock_calls == expected_output_calls
        assert hdawg_mock.awgs[i].output2.mock_calls == expected_output_calls


def test_validate_schedule(
    empty_schedule: Schedule,
    basic_schedule: Schedule,
    schedule_with_pulse_info: Schedule,
):
    with pytest.raises(ValueError) as execinfo:
        zhinst_backend._validate_schedule(empty_schedule)

    assert "Undefined timing contraints for schedule 'Empty Experiment'!" == str(
        execinfo.value
    )

    with pytest.raises(ValueError) as execinfo:
        zhinst_backend._validate_schedule(basic_schedule)

    assert (
        "Absolute timing has not been determined for the schedule 'Basic schedule'!"
        == str(execinfo.value)
    )

    zhinst_backend._validate_schedule(schedule_with_pulse_info)


def test_uhfqa_sequence1(
    mocker,
    create_uhfqa_mock,
    create_schedule_with_pulse_info,
    uhfqa_hardware_map: Dict[str, Any],
) -> None:
    # Arrange
    schedule = Schedule("test")
    schedule.add(Reset("q0"))
    schedule.add(X("q0"))
    schedule.add(Measure("q0"))
    schedule = create_schedule_with_pulse_info(schedule)

    uhfqa_mock: uhfqa.UHFQA = create_uhfqa_mock()
    mocker.patch(
        "qcodes.instrument.base.Instrument.find_instrument",
        return_value=uhfqa_mock,
    )
    mocker.patch.object(zi_helpers, "set_value")
    modulate_wave_spy = mocker.patch.object(
        waveforms, "modulate_wave", wraps=waveforms.modulate_wave
    )
    set_integration_weights_mock = mocker.patch.object(
        zi_helpers, "set_integration_weights"
    )
    np_savetext_mock = mocker.patch.object(np, "savetxt")
    write_seqc_file_mock = mocker.patch.object(
        zi_helpers, "write_seqc_file", return_value=Path("awg-0.seqc")
    )

    expected_seqc = dedent(
        """\
    // Generated by quantify-scheduler.
    // Variables
    var __repetitions__ = 1;
    wave w0 = "dev2299_wave0";\n
    // Operations
    // Schedule offset: 0.0002s 45000 clocks
    // Schedule duration: 4.3600000000000735e-07s 98 clocks
    // Sequence start: 1.6000000000007978e-08s 4 clocks
    // Sequence duration: 3.000000000000073e-07s 67 clocks
    // Sequence end: 3.1600000000001527e-07s 71 clocks
    // Dead time: 5e-06s 1125 clocks
    // Line delay: -1s 0 clocks
    repeat(__repetitions__)
    {
      waitDigTrigger(2, 1);	// clock=0
      wait(1);	// clock=0 n_instr=3
      playWave(w0);	// clock=4 n_instr=3
      wait(21);	// clock=4 n_instr=3
      setTrigger(AWG_INTEGRATION_ARM + AWG_INTEGRATION_TRIGGER + AWG_MONITOR_TRIGGER);	// clock=31 n_instr=3
      // Final wait + dead time
      wait(1190);	// clock=99
    }
    """
    ).lstrip("\n")

    # Act
    zhinst_backend.setup_zhinst_backend(schedule, uhfqa_hardware_map)

    # Assert
    modulate_wave_spy.assert_called()
    set_integration_weights_mock.assert_called()
    np_savetext_mock.assert_called()
    write_seqc_file_mock.assert_called()
    # Note: Assert inner variable for better error messsage
    args, _ = write_seqc_file_mock.call_args
    assert args[1] == expected_seqc
    write_seqc_file_mock.assert_called_with(
        uhfqa_mock.awg, expected_seqc, "uhfqa_awg.seqc"
    )


def test_uhfqa_sequence2(
    mocker,
    create_uhfqa_mock,
    create_schedule_with_pulse_info,
    uhfqa_hardware_map: Dict[str, Any],
) -> None:
    # Arrange
    schedule = raw_trace_schedule(
        port="q0:res",
        clock="q0.ro",
        integration_time=1e-6,
        spec_pulse_amp=1,
        frequency=7.04e9,
    )
    schedule = create_schedule_with_pulse_info(schedule)

    uhfqa_mock: uhfqa.UHFQA = create_uhfqa_mock()
    mocker.patch(
        "qcodes.instrument.base.Instrument.find_instrument",
        return_value=uhfqa_mock,
    )
    mocker.patch.object(zi_helpers, "set_value")
    modulate_wave_spy = mocker.patch.object(
        waveforms, "modulate_wave", wraps=waveforms.modulate_wave
    )
    set_integration_weights_mock = mocker.patch.object(
        zi_helpers, "set_integration_weights"
    )
    np_savetext_mock = mocker.patch.object(np, "savetxt")
    write_seqc_file_mock = mocker.patch.object(
        zi_helpers, "write_seqc_file", return_value=Path("awg-0.seqc")
    )

    expected_seqc = dedent(
        """\
    // Generated by quantify-scheduler.
    // Variables
    var __repetitions__ = 1;
    wave w0 = "dev2299_wave0";\n
    // Operations
    // Schedule offset: 1e-06s 225 clocks
    // Schedule duration: 1.5999999999999997e-06s 360 clocks
    // Sequence start: 0.0s 0 clocks
    // Sequence duration: 1.5999999999999997e-06s 360 clocks
    // Sequence end: 1.5999999999999997e-06s 360 clocks
    // Dead time: 5e-06s 1125 clocks
    // Line delay: -1s 0 clocks
    repeat(__repetitions__)
    {
      waitDigTrigger(2, 1);	// clock=0
      setTrigger(AWG_INTEGRATION_ARM + AWG_INTEGRATION_TRIGGER + AWG_MONITOR_TRIGGER);	// clock=0 n_instr=3
      wait(332);	// clock=0 n_instr=3
      playWave(w0);	// clock=338 n_instr=3
      // Final wait + dead time
      wait(1146);	// clock=362
    }
    """
    ).lstrip("\n")

    # Act
    zhinst_backend.setup_zhinst_backend(schedule, uhfqa_hardware_map)

    # Assert
    modulate_wave_spy.assert_called()
    set_integration_weights_mock.assert_called()
    np_savetext_mock.assert_called()
    write_seqc_file_mock.assert_called()
    # Note: Assert inner variable for better error messsage
    args, _ = write_seqc_file_mock.call_args
    assert args[1] == expected_seqc
    write_seqc_file_mock.assert_called_with(
        uhfqa_mock.awg, expected_seqc, "uhfqa_awg.seqc"
    )


def test_uhfqa_sequence3(
    mocker,
    create_uhfqa_mock,
    create_schedule_with_pulse_info,
    uhfqa_hardware_map: Dict[str, Any],
) -> None:
    # Arrange
    schedule = ssb_integration_complex_schedule(
        port="q0:res",
        clock="q0.ro",
        integration_time=1e-6,
        spec_pulse_amp=1,
        frequency=7.04e9,
    )
    schedule = create_schedule_with_pulse_info(schedule)

    uhfqa_mock: uhfqa.UHFQA = create_uhfqa_mock()
    mocker.patch(
        "qcodes.instrument.base.Instrument.find_instrument",
        return_value=uhfqa_mock,
    )
    mocker.patch.object(zi_helpers, "set_value")
    modulate_wave_spy = mocker.patch.object(
        waveforms, "modulate_wave", wraps=waveforms.modulate_wave
    )
    set_integration_weights_mock = mocker.patch.object(
        zi_helpers, "set_integration_weights"
    )
    np_savetext_mock = mocker.patch.object(np, "savetxt")
    write_seqc_file_mock = mocker.patch.object(
        zi_helpers, "write_seqc_file", return_value=Path("awg-0.seqc")
    )

    expected_seqc = dedent(
        """\
    // Generated by quantify-scheduler.
    // Variables
    var __repetitions__ = 1;
    wave w0 = "dev2299_wave0";
    wave w1 = "dev2299_wave1";\n
    // Operations
    // Schedule offset: 1e-06s 225 clocks
    // Schedule duration: 1.1999999999999997e-06s 270 clocks
    // Sequence start: 0.0s 0 clocks
    // Sequence duration: 1.1999999999999997e-06s 270 clocks
    // Sequence end: 1.1999999999999997e-06s 270 clocks
    // Dead time: 5e-06s 1125 clocks
    // Line delay: -1s 0 clocks
    repeat(__repetitions__)
    {
      waitDigTrigger(2, 1);	// clock=0
      setTrigger(AWG_INTEGRATION_ARM + AWG_INTEGRATION_TRIGGER + AWG_MONITOR_TRIGGER);	// clock=0 n_instr=3
      wait(107);	// clock=0 n_instr=3
      playWave(w0);	// clock=113 n_instr=3
      wait(16);	// clock=113 n_instr=3
      setTrigger(AWG_INTEGRATION_ARM + AWG_INTEGRATION_TRIGGER + AWG_MONITOR_TRIGGER);	// clock=135 n_instr=3
      wait(106);	// clock=135 n_instr=3
      playWave(w1);	// clock=247 n_instr=3
      // Final wait + dead time
      wait(1146);	// clock=271
    }
    """
    ).lstrip("\n")

    # Act
    zhinst_backend.setup_zhinst_backend(schedule, uhfqa_hardware_map)

    # Assert
    modulate_wave_spy.assert_called()
    set_integration_weights_mock.assert_called()
    np_savetext_mock.assert_called()
    write_seqc_file_mock.assert_called()
    # Note: Assert inner variable for better error messsage
    args, _ = write_seqc_file_mock.call_args
    assert args[1] == expected_seqc
    write_seqc_file_mock.assert_called_with(
        uhfqa_mock.awg, expected_seqc, "uhfqa_awg.seqc"
    )


def test__program_modulation_PREMODULATE(
    mocker,
    uhfqa_hardware_map: Dict[str, Any],
    create_device,
    create_uhfqa_mock,
    create_schedule_with_pulse_info,
):
    # Arrange
    uhfqa = create_uhfqa_mock()
    device: Device = create_device(uhfqa_hardware_map)
    device.type = DeviceType.UHFQA
    device.channel_0.modulation = ModulationModeType.PREMODULATE

    schedule: Schedule = create_schedule_with_pulse_info()

    clock_rate = uhfqa.awg._awg.sequence_params["sequence_parameters"]["clock_rate"]
    pulseid_pulseinfo_dict = get_pulse_info_by_uuid(schedule)
    pulseid_waveformfn_dict: Dict[int, GetWaveformPartial] = get_waveform_by_pulseid(
        schedule
    )
    waveforms_dict: Dict[int, np.ndarray] = dict()
    for pulse_id, waveform_partial_fn in pulseid_waveformfn_dict.items():
        waveforms_dict[pulse_id] = waveform_partial_fn(sampling_rate=clock_rate)

    modulate_wave_spy = mocker.patch.object(
        waveforms, "modulate_wave", wraps=waveforms.modulate_wave
    )

    # Act
    zhinst_backend._program_modulation(
        uhfqa.awg, device, device.channel_0, waveforms_dict, pulseid_pulseinfo_dict
    )

    # Assert
    modulate_wave_spy.assert_called()


@pytest.mark.parametrize("device_type", [(DeviceType.UHFQA), (DeviceType.HDAWG)])
def test__program_modulation_MODULATE(
    device_type: DeviceType,
    uhfqa_hardware_map: Dict[str, Any],
    hdawg_hardware_map: Dict[str, Any],
    create_device,
    create_uhfqa_mock,
    create_hdawg_mock,
):
    # Arrange
    if device_type == DeviceType.HDAWG:
        instrument = create_hdawg_mock(4)
        device: Device = create_device(hdawg_hardware_map)
        device.type = device_type
        awg = instrument.awgs[0]
    elif device_type == DeviceType.UHFQA:
        instrument = create_uhfqa_mock()
        device: Device = create_device(uhfqa_hardware_map)
        device.type = device_type
        awg = instrument.awg

    device.channel_0.modulation = ModulationModeType.MODULATE

    # Act
    zhinst_backend._program_modulation(awg, device, device.channel_0, dict(), dict())

    # Assert
    if device_type == DeviceType.HDAWG:
        output = device.channel_0
        awg.enable_iq_modulation.assert_called()
        awg.modulation_freq.assert_called_with(output.lo_freq + output.interm_freq)
        awg.modulation_phase_shift.assert_called_with(output.phase_shift)
        awg.gain1.assert_called_with(output.gain1)
        awg.gain2.assert_called_with(output.gain2)
    else:
        assert hasattr(awg, "enable_iq_modulation") == False


@pytest.mark.parametrize("device_type", [(DeviceType.UHFQA), (DeviceType.HDAWG)])
def test__program_modulation_NONE(
    device_type: DeviceType,
    uhfqa_hardware_map: Dict[str, Any],
    hdawg_hardware_map: Dict[str, Any],
    create_device,
    create_uhfqa_mock,
    create_hdawg_mock,
):
    # Arrange
    if device_type == DeviceType.HDAWG:
        instrument = create_hdawg_mock(4)
        device: Device = create_device(hdawg_hardware_map)
        device.type = device_type
        awg = instrument.awgs[0]
    elif device_type == DeviceType.UHFQA:
        instrument = create_uhfqa_mock()
        device: Device = create_device(uhfqa_hardware_map)
        device.type = device_type
        awg = instrument.awg

    device.channel_0.modulation = ModulationModeType.NONE

    # Act
    zhinst_backend._program_modulation(awg, device, device.channel_0, dict(), dict())

    # Assert
    if device_type == DeviceType.HDAWG:
        awg.disable_iq_modulation.assert_called()
    else:
        assert hasattr(awg, "disable_iq_modulation") == False


def test__set_waveforms_WAVEFORM_TABLE(mocker, create_hdawg_mock):
    # Arrange
    instrument = create_hdawg_mock(4)
    awg = instrument.awgs[0]
    waveform = np.vectorize(complex)(np.zeros(1024), np.ones(1024))
    waveforms_dict = {0: waveform}
    commandtable_map = {0: 0}
    pulseid_pulseinfo_dict = {0: {"wf_func": "square"}}

    set_wave_vector_mock = mocker.patch.object(zi_helpers, "set_wave_vector")

    _data = np.zeros((2, 1024))
    _data[0] = np.real(waveform)
    _data[1] = np.imag(waveform)
    expected_data = (_data.reshape((-2,), order="F") * (2 ** 15 - 1)).astype("int16")

    # Act
    zhinst_backend._set_waveforms(
        instrument,
        awg,
        waveforms_dict,
        commandtable_map,
        pulseid_pulseinfo_dict,
        _WaveformDestination.WAVEFORM_TABLE,
    )

    # Assert
    expected_call = [call(instrument, awg._awg._index, 0, ANY)]
    assert set_wave_vector_mock.call_args_list == expected_call
    args, _ = set_wave_vector_mock.call_args
    np.testing.assert_array_equal(args[3], expected_data)


def test__set_waveforms_CSV(mocker, create_hdawg_mock):
    # Arrange
    instrument = create_hdawg_mock(4)
    awg = instrument.awgs[0]
    waveform = np.vectorize(complex)(np.zeros(1024), np.ones(1024))
    waveforms_dict = {0: waveform}
    commandtable_map = {0: 0}
    pulseid_pulseinfo_dict = {0: {"wf_func": "square"}}

    np_savetext_mock = mocker.patch.object(np, "savetxt")
    expected_path = Path(".").joinpath(
        "awg", "waves", f"{instrument._serial}_wave{commandtable_map[0]}.csv"
    )
    _data = np.zeros((2, 1024))
    _data[0] = np.real(waveform)
    _data[1] = np.imag(waveform)
    _scaled_data = (_data.reshape((-2,), order="F") * (2 ** 15 - 1)).astype("int16")
    expected_data = np.reshape(_scaled_data, (1024, -1))

    # Act
    zhinst_backend._set_waveforms(
        instrument,
        awg,
        waveforms_dict,
        commandtable_map,
        pulseid_pulseinfo_dict,
        _WaveformDestination.CSV,
    )

    # Assert
    expected_call = [call(expected_path, ANY, delimiter=";")]
    assert np_savetext_mock.call_args_list == expected_call
    args, _ = np_savetext_mock.call_args
    np.testing.assert_array_equal(args[1], expected_data)