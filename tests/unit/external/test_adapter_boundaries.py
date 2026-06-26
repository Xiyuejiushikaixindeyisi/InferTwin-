from pathlib import Path

import pytest

from infertwin.external.aiconfigurator import AIConfiguratorAdapter
from infertwin.external.aiconfigurator_git import (
    AiconfiguratorGitEstimateRequest,
    AiconfiguratorGitReference,
)
from infertwin.external.mksim import MKSimAdapter
from infertwin.external.ramulator2 import Ramulator2Adapter
from infertwin.latency.base import KVRestoreEstimateInput, PrefillEstimateInput


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


def test_aiconfigurator_git_reference_uses_public_test_name(tmp_path: Path) -> None:
    _make_aiconfigurator_git_checkout(tmp_path)
    reference = AiconfiguratorGitReference(repo_path=tmp_path)

    reference.validate_checkout()
    args = reference.build_estimate_cli_args(
        AiconfiguratorGitEstimateRequest(
            model_path="Qwen/Qwen3-32B",
            system_name="h200_sxm",
            batch_size=4,
            isl=2048,
            osl=1,
            tp_size=2,
            prefix_tokens=512,
        )
    )

    assert reference.source_name == "aiconfigurator_git"
    assert reference.package_name == "aiconfigurator"
    assert args == (
        "cli",
        "estimate",
        "--model-path",
        "Qwen/Qwen3-32B",
        "--system",
        "h200_sxm",
        "--backend",
        "vllm",
        "--estimate-mode",
        "agg",
        "--batch-size",
        "4",
        "--isl",
        "2048",
        "--osl",
        "1",
        "--tp-size",
        "2",
        "--pp-size",
        "1",
        "--prefix",
        "512",
    )


def test_aiconfigurator_git_reference_rejects_incomplete_checkout(tmp_path: Path) -> None:
    reference = AiconfiguratorGitReference(repo_path=tmp_path)

    with pytest.raises(FileNotFoundError, match="aiconfigurator_git checkout is incomplete"):
        reference.validate_checkout()


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


def _make_aiconfigurator_git_checkout(path: Path) -> None:
    (path / "docs").mkdir()
    (path / "src" / "aiconfigurator" / "cli").mkdir(parents=True)
    (path / "pyproject.toml").write_text("[project]\nname = 'aiconfigurator'\n")
    (path / "README.md").write_text("# aiconfigurator\n")
    (path / "docs" / "cli_user_guide.md").write_text("# CLI User Guide\n")
    (path / "src" / "aiconfigurator" / "cli" / "api.py").write_text(
        "def cli_estimate():\n    pass\n"
    )
