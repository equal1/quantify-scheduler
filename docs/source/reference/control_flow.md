---
jupytext:
  text_representation:
    extension: .md
    format_name: myst
    format_version: 0.13
    jupytext_version: 1.14.6
kernelspec:
  display_name: Python 3 (ipykernel)
  language: python
  name: python3
---

(sec-control-flow)=
# Control flow

Complex schedules can be constructed from pulses, gates and schedules using control flow. When adding an Operation or Schedule to another Schedule, a `control_flow` argument can be specified.

(sec-control-flow-subschedule)=
## Adding schedules to a schedule

- Supported by {mod}`Qblox <quantify_scheduler.backends.qblox>` and
  {mod}`Zurich Instruments <quantify_scheduler.backends.zhinst>` backends.

A schedule can be added to a schedule just like an operation. This does not require the use of the `control_flow` argument. 

This is useful e.g. to define a custom composite gate:
```{code-cell} ipython3
from quantify_scheduler.operations.gate_library import X, Y90
from quantify_scheduler import Schedule

def hadamard(qubit: str) -> Schedule:
    hadamard_sched = Schedule("hadamard")
    hadamard_sched.add(X(qubit))
    hadamard_sched.add(Y90(qubit))
    return hadamard_sched

my_schedule = Schedule("nice_experiment")
my_schedule.add(X("q1"))
my_schedule.add(hadamard("q1"))
```

Note: The `repetitions` argument of all but the outermost Schedules is ignored. Schedules can be nested arbitrarily. Timing constraints relative to an inner schedule interpret the inner schedule as one continuous operation. It is not possible to use an operation within a subschedule from outside as reference operation.

(sec-control-flow-loops)=
## Repetition loops

- Supported by {mod}`Qblox <quantify_scheduler.backends.qblox>` backend.

If the `control_flow` argument of `Schedule.add` receives an instance of the `Loop` operation, the added Operation or Schedule will be repeated as specified.

This can be used to efficiently implement sequential averaging without running over the instruction limit of the hardware:
```{code-cell} ipython3
import numpy as np
from typing import Union
from quantify_scheduler.operations.control_flow_library import Loop
from quantify_scheduler.operations.gate_library import Reset, Measure

def t1_sched_sequential(
    times: Union[np.ndarray, float],
    qubit: str,
    repetitions: int = 1,
) -> Schedule:
    times = np.asarray(times)
    times = times.reshape(times.shape or (1,))

    schedule = Schedule("T1")
    for i, tau in enumerate(times):
        inner = Schedule(f"inner_{i}")
        inner.add(Reset(qubit), label=f"Reset {i}")
        inner.add(X(qubit), label=f"pi {i}")
        inner.add(
            Measure(qubit, acq_index=i),
            ref_pt="start",
            rel_time=tau,
            label=f"Measurement {i}",
        )
        schedule.add(inner, control_flow=Loop(repetitions))
    return schedule
```
Hardware averaging works as expected. In `BinMode.APPEND` binning mode, the data is returned in chronological order.

```{note}
Loops are an experimental feature and come with several limitations at this time, see below.
```

Limitations:
1. The time order for zero-duration assembly instructions may be incorrect, so verify the compiled schedule (via the generated assembly code). Using loops to implement sequential averaging for qubit spectroscopy is verified to work as expected. Known issues occur in using `SetClockFrequency` and `SquarePulse` with duration > 1us at the beginning or end of a loop, for example:
```{code-cell} ipython3
from quantify_scheduler.operations.pulse_library import SquarePulse
schedule = Schedule("T1")
schedule.add(
    SquarePulse(
        amp=0.3,
        phase=0,
        port="q0:res",
        duration=2e-6,
        clock="q0.ro",
    ),
    control_flow=Loop(3),
)
```
2. Repetition loops act on all port-clock combinations present in the circuit. This means that both `X("q0")` and `Y90("q1")` in the following circuit are repeated three times:
```{code-cell} ipython3
schedule = Schedule("T1")
x = schedule.add(X("q0"), control_flow=Loop(3))
schedule.add(Y90("q1"), ref_op=x, ref_pt="start", rel_time=0)
```
3. To avoid unexpected timing issues, it is strongly recommended to use loops only with subschedules, with no operations overlapping with the subschedule.