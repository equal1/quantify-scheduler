# Repository: https://gitlab.com/quantify-os/quantify-scheduler
# Licensed according to the LICENCE file on the main branch
"""Example old-style Qblox hardware config dictionary for legacy support."""

hardware_config = {
    "backend": "quantify_scheduler.backends.qblox_backend.hardware_compile",
    "latency_corrections": {"q4:mw-q4.01": 8e-9, "q5:mw-q5.01": 4e-9},
    "distortion_corrections": {
        "q0:fl-cl0.baseband": {
            "filter_func": "scipy.signal.lfilter",
            "input_var_name": "x",
            "kwargs": {"b": [0, 0.25, 0.5], "a": [1]},
            "clipping_values": [-2.5, 2.5],
        }
    },
    "cluster0": {
        "ref": "internal",
        "instrument_type": "Cluster",
        "sequence_to_file": False,
        "cluster0_module1": {
            "instrument_type": "QCM",
            "sequence_to_file": False,
            "complex_output_0": {
                "lo_name": "lo0",
                "dc_mixer_offset_I": 0.0,
                "dc_mixer_offset_Q": 0.0,
                "downconverter_freq": None,
                "marker_debug_mode_enable": True,
                "mix_lo": True,
                "portclock_configs": [
                    {
                        "port": "q4:mw",
                        "clock": "q4.01",
                        "interm_freq": 200e6,
                        "mixer_amp_ratio": 0.9999,
                        "mixer_phase_error_deg": -4.2,
                    }
                ],
            },
        },
        "cluster0_module2": {
            "instrument_type": "QCM_RF",
            "sequence_to_file": False,
            "complex_output_0": {
                "lo_freq": None,
                "output_att": 4,
                "portclock_configs": [
                    {"port": "q5:mw", "clock": "q5.01", "interm_freq": 50e6}
                ],
            },
            "complex_output_1": {
                "lo_freq": 5e9,
                "output_att": 6,
                "portclock_configs": [
                    {"port": "q6:mw", "clock": "q6.01", "interm_freq": None}
                ],
            },
        },
        "cluster0_module3": {
            "instrument_type": "QRM",
            "sequence_to_file": False,
            "complex_output_0": {
                "lo_name": "lo1",
                "dc_mixer_offset_I": -0.054,
                "dc_mixer_offset_Q": -0.034,
                "input_gain_I": 2,
                "input_gain_Q": 3,
                "portclock_configs": [
                    {
                        "mixer_amp_ratio": 0.9997,
                        "mixer_phase_error_deg": -4.0,
                        "port": "q4:res",
                        "clock": "q4.ro",
                        "interm_freq": None,
                    }
                ],
            },
        },
        "cluster0_module4": {
            "instrument_type": "QRM_RF",
            "sequence_to_file": False,
            "complex_input_0": {
                "lo_freq": None,
                "input_att": 10,
                "portclock_configs": [
                    {"port": "q5:res", "clock": "q5.ro", "interm_freq": 50e6}
                ],
            },
            "complex_output_0": {
                "lo_freq": 7.8e9,
                "output_att": 12,
                "input_att": 4,
                "portclock_configs": [
                    {"port": "q0:res", "clock": "q0.ro", "interm_freq": None}
                ],
            },
        },
        "cluster0_module10": {
            "instrument_type": "QCM",
            "sequence_to_file": False,
            "real_output_0": {
                "portclock_configs": [{"port": "q0:fl", "clock": "cl0.baseband"}]
            },
            "real_output_1": {
                "portclock_configs": [{"port": "q1:fl", "clock": "cl0.baseband"}]
            },
            "real_output_2": {
                "portclock_configs": [{"port": "q2:fl", "clock": "cl0.baseband"}]
            },
            "real_output_3": {
                "portclock_configs": [{"port": "q3:fl", "clock": "cl0.baseband"}]
            },
        },
        "cluster0_module12": {
            "instrument_type": "QCM",
            "sequence_to_file": False,
            "real_output_0": {
                "portclock_configs": [{"port": "q4:fl", "clock": "cl0.baseband"}]
            },
        },
    },
    "lo0": {"instrument_type": "LocalOscillator", "frequency": None, "power": 1},
    "lo1": {"instrument_type": "LocalOscillator", "frequency": 7.2e9, "power": 1},
}
