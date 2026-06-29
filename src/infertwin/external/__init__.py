"""Adapters and calibration boundaries for external simulators and projects."""

from infertwin.external.kv_load_calibration import (
    KVLoadCalibrationFit,
    KVLoadCalibrationObservation,
    fit_byte_linear_v1,
    fit_token_linear_v1,
    to_kv_load_profile_mapping,
)
from infertwin.external.mooncake import MooncakeCalibrationReference
from infertwin.external.ramulator2 import Ramulator2CalibrationReference

__all__ = [
    "KVLoadCalibrationFit",
    "KVLoadCalibrationObservation",
    "MooncakeCalibrationReference",
    "Ramulator2CalibrationReference",
    "fit_byte_linear_v1",
    "fit_token_linear_v1",
    "to_kv_load_profile_mapping",
]
