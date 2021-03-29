# -----------------------------------------------------------------------------
# Description:    Backend for Zurich Instruments.
# Repository:     https://gitlab.com/quantify-os/quantify-scheduler
# Copyright (C) Qblox BV & Orange Quantum Systems Holding BV (2020-2021)
# -----------------------------------------------------------------------------
from __future__ import annotations

from typing import Tuple

import numpy as np
from zhinst import qcodes

from quantify.scheduler.backends.zhinst import helpers as zi_helpers


def monitor_acquisition_resolver(
    uhfqa: qcodes.UHFQA, monitor_nodes: Tuple[str, str]
) -> np.ndarray:
    """
    Returns complex value of UHFQA Monitor nodes.

    Parameters
    ----------
    uhfqa :
    monitor_nodes :

    Returns
    -------
    np.ndarray
    """
    (node_i, node_q) = monitor_nodes
    results_i = zi_helpers.get_value(uhfqa, node_i)
    results_q = zi_helpers.get_value(uhfqa, node_q)
    return np.vectorize(complex)(results_i, results_q)


def result_acquisition_resolver(uhfqa: qcodes.UHFQA, result_node: str) -> np.ndarray:
    """
    Returns complex value of UHFQA Result node.

    Parameters
    ----------
    uhfqa :
    result_node :

    Returns
    -------
    np.ndarray
    """
    return zi_helpers.get_value(uhfqa, result_node)
