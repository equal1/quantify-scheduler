# Repository: https://gitlab.com/quantify-os/quantify-scheduler
# Licensed according to the LICENCE file on the main branch
r"""
Module containing :class:`~quantify_core.measurement.types.ProfiledGettable`\s
for use with quantify-scheduler.

.. warning::

    The ProfiledGettable is currently only tested to support Qblox hardware.
"""

import logging
import time
import json
import os
from datetime import datetime
import numpy as np

import matplotlib.pyplot as plt

from quantify_scheduler.gettables import ScheduleGettable
from quantify_scheduler.instrument_coordinator import InstrumentCoordinator

logger = logging.getLogger(__name__)


def profiler(func):
    """Decorator that reports the execution time."""

    def wrap(self, *args, **kwargs):
        start = time.time()
        result = func(self, *args, **kwargs)
        end = time.time()
        if not self.profile.get(func.__name__):
            self.profile[func.__name__] = []
        self.profile[func.__name__].append(end - start)
        return result

    return wrap


class ProfiledInstrumentCoordinator(InstrumentCoordinator):
    """
    This subclass implements a profiling tool to log the timing results.
    """

    def __init__(self, name: str, parentinstrumentcoordinator):
        self.profile = {"schedule": []}
        super().__init__(name, add_default_generic_icc=False)
        self.parent_ic = parentinstrumentcoordinator

    def _get_schedule_time(self, compiled_schedule):
        """
        Method to find the length of the schedule. As operations can be
        executed in parallel, the operation with the last timestamp does not
        necessarily end last. To omit this, the addition of the duration is
        included in the for loop.

        """

        # find last timestamp
        schedule_time = 0
        for schedulable in compiled_schedule.schedulables.items():
            time_stamp = schedulable[-1].data["abs_time"]
            label = schedulable[-1].data["operation_repr"]

            # find duration of last operation
            operation = compiled_schedule["operation_dict"][label]

            pulses = [
                pulse.get("duration") + pulse.get("t0")
                for pulse in operation.data["pulse_info"]
            ]
            acquisitions = [
                acquisition.get("duration") + acquisition.get("t0")
                for acquisition in operation.data["acquisition_info"]
            ]
            last_pulse = max(pulses, default=0)
            last_acq = max(acquisitions, default=0)
            final_op_len = max([last_pulse, last_acq], default=0)

            tmp_time = time_stamp + final_op_len

            if tmp_time > schedule_time:
                schedule_time = tmp_time
        schedule_time *= compiled_schedule.repetitions
        self.profile["schedule"].append(schedule_time)

    @profiler
    def add_component(
        self,
        component,
    ) -> None:
        self.parent_ic.add_component(component)

    @profiler
    def prepare(
        self,
        compiled_schedule,
    ) -> None:
        self._get_schedule_time(compiled_schedule)
        self.parent_ic.prepare(compiled_schedule)

    @profiler
    def start(self):
        self.parent_ic.start()

    @profiler
    def stop(self, allow_failure=False):
        self.parent_ic.stop()

    @profiler
    def retrieve_acquisition(self):
        return self.parent_ic.retrieve_acquisition()

    @profiler
    def wait_done(self, timeout_sec: int = 10):
        self.parent_ic.wait_done(timeout_sec)


class ProfiledGettable(ScheduleGettable):
    """
    Subclass to overwite the initialize method, in order to include
    compilation in the profiling.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.profile = {"compile": []}

        # overwrite linked IC to a profiled IC
        instr_coordinator = self.quantum_device.instr_instrument_coordinator.get_instr()
        self.profiled_instr_coordinator = ProfiledInstrumentCoordinator(
            "profiled_IC", instr_coordinator
        )
        self.quantum_device.instr_instrument_coordinator("profiled_IC")

    def _compile(self, sched):
        """Overwrite compile step ofr profiling."""
        start = time.time()
        super()._compile(sched)
        stop = time.time()
        self.profile["compile"].append(stop - start)

    def log_profiles(
        self, path="profiling_log{}.json".format(datetime.now().strftime("%m%d%H%M"))
    ):
        """
        Store time logs to json file.

        """
        profile = self.profile.copy()
        profile.update(self.profiled_instr_coordinator.profile)

        if not os.path.exists("profiling_logs"):
            os.makedirs("profiling_logs")
        with open("profiling_logs/{}".format(path), "w") as file:
            json.dump(profile, file, indent=4, separators=(",", ": "))

    def plot_profile(self, plot_name="average_runtimes.pdf"):
        """Create barplot of accumulated profiling data."""
        profile = self.profile.copy()
        profile.update(self.profiled_instr_coordinator.profile)
        time_ax = list(profile.keys())
        num_keys = len(time_ax)
        x_pos = np.arange(num_keys)
        means = [np.mean(x) for x in profile.values()]
        error = [np.std(x) for x in profile.values()]
        fig, ax = plt.subplots(figsize=(9, 6))
        color = [
            "xkcd:bright blue",
            "xkcd:sky blue",
            "xkcd:sea blue",
            "xkcd:turquoise blue",
            "xkcd:aqua",
            "xkcd:cyan",
        ][:num_keys]
        ax.bar(x_pos, means, yerr=error, color=color)
        ax.bar(num_keys, means[0], color=color[0])
        for i in range(1, num_keys):
            ax.bar(num_keys, means[i], color=color[i], bottom=sum(means[:i]))
        time_ax.append("total")
        ax.set_xticks(np.append(x_pos, num_keys))
        ax.set_xticklabels(time_ax)
        plt.ylabel("runtime [s]")
        plt.title("Average runtimes")
        fig.savefig(plot_name)
