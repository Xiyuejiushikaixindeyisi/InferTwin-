from pathlib import Path

import pytest

from hitfloor.external.aiconfigurator import AIConfiguratorAdapter
from hitfloor.external.mksim import MKSimAdapter
from hitfloor.external.ramulator2 import Ramulator2Adapter
from hitfloor.latency.base import KVRestoreEstimateInput, PrefillEstimateInput


def test_aiconfigurator_adapter_fails_explicitly_until_schema_mapping_exists() -> None:
    adapter = AIConfiguratorAdapter(executable=Path("aiconfigurator"))

    with pytest.raises(NotImplementedError, match="AIConfigurator"):
        adapter.estimate_prefill(
            PrefillEstimateInput(
                cached_prefix_tokens=0,
                uncached_suffix_tokens=128,
                batch_request_count=4,
            )
        )


def test_mksim_adapter_fails_explicitly_until_schema_mapping_exists() -> None:
    adapter = MKSimAdapter(executable=Path("mksim"))

    with pytest.raises(NotImplementedError, match="MKsim"):
        adapter.estimate_prefill(
            PrefillEstimateInput(
                cached_prefix_tokens=0,
                uncached_suffix_tokens=128,
                batch_request_count=4,
            )
        )


def test_ramulator2_adapter_fails_explicitly_until_schema_mapping_exists() -> None:
    adapter = Ramulator2Adapter(executable=Path("ramulator2"))

    with pytest.raises(NotImplementedError, match="Ramulator2"):
        adapter.estimate_kv_restore(KVRestoreEstimateInput(hbm_hit_tokens=128, ddr_hit_tokens=0))
