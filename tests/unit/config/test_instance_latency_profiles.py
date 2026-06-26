import pytest

from infertwin.config.profiles import InstanceProfile


def test_instance_profile_parses_instance_latency_profiles() -> None:
    profile = InstanceProfile.from_mapping(_instance_profile_mapping())

    assert profile.deployment_by_instance == {
        "instance-a": "glm-v5.1-vllm-ascend-prefill",
        "instance-b": "glm-v5.1-vllm-ascend-prefill",
    }
    assert {instance.instance_uuid: instance.latency_profile for instance in profile.instances} == {
        "instance-a": "instance-a-ttft",
        "instance-b": "instance-b-ttft",
    }
    assert {instance.instance_uuid: instance.model_name for instance in profile.instances} == {
        "instance-a": "glm-v5.1",
        "instance-b": "glm-v5.1",
    }
    latency_by_instance = profile.latency_profile_by_instance
    assert latency_by_instance["instance-a"].model_name == "glm-v5.1"
    assert latency_by_instance["instance-a"].hardware_name == "ascend-a3-fast"
    assert latency_by_instance["instance-a"].fitted_ttft.ms_per_uncached_token == 0.01
    assert latency_by_instance["instance-a"].fitted_ttft.calibration_window_requests == 500
    assert latency_by_instance["instance-a"].kv_load.ddr_ms_per_cached_token == 0.001
    assert latency_by_instance["instance-a"].kv_load.remote_ms_per_cached_token == 0.003
    assert latency_by_instance["instance-b"].hardware_name == "ascend-a3-slow"
    assert latency_by_instance["instance-b"].fitted_ttft.ms_per_uncached_token == 0.02
    assert latency_by_instance["instance-b"].kv_load.ddr_ms_per_cached_token == 0.0
    assert latency_by_instance["instance-b"].kv_load.remote_ms_per_cached_token == 0.0


def test_instance_profile_keeps_legacy_deployment_only_schema() -> None:
    profile = InstanceProfile.from_mapping(
        {
            "instances": {
                "name": "legacy-cluster",
                "items": {
                    "instance-a": {"deployment": "shared-deployment"},
                    "instance-b": {"deployment": "shared-deployment"},
                },
            }
        }
    )

    assert profile.deployment_by_instance == {
        "instance-a": "shared-deployment",
        "instance-b": "shared-deployment",
    }
    assert {instance.instance_uuid: instance.model_name for instance in profile.instances} == {
        "instance-a": None,
        "instance-b": None,
    }
    assert profile.latency_profiles == ()
    assert profile.latency_profile_by_instance == {}


def test_instance_latency_profiles_allow_shared_deployment_with_different_ttft() -> None:
    profile = InstanceProfile.from_mapping(_instance_profile_mapping())

    deployments = {item.deployment for item in profile.instances}
    ttft_slopes = {item.fitted_ttft.ms_per_uncached_token for item in profile.latency_profiles}

    assert deployments == {"glm-v5.1-vllm-ascend-prefill"}
    assert ttft_slopes == {0.01, 0.02}


def test_instance_profile_allows_missing_latency_profile_when_table_is_declared() -> None:
    data = _instance_profile_mapping()
    del data["instances"]["items"]["instance-b"]["latency_profile"]

    profile = InstanceProfile.from_mapping(data)

    instance_b = next(item for item in profile.instances if item.instance_uuid == "instance-b")
    assert instance_b.latency_profile is None


def test_instance_profile_rejects_unknown_latency_profile_reference() -> None:
    data = _instance_profile_mapping()
    data["instances"]["items"]["instance-b"]["latency_profile"] = "missing-profile"

    with pytest.raises(ValueError, match="references unknown latency profile"):
        InstanceProfile.from_mapping(data)


def test_instance_profile_rejects_unsupported_latency_backend() -> None:
    data = _instance_profile_mapping()
    data["instances"]["latency_profiles"]["instance-a-ttft"]["backend"] = "formula"

    with pytest.raises(ValueError, match="backend only supports fitted_ttft"):
        InstanceProfile.from_mapping(data)


def test_instance_profile_rejects_invalid_fitted_ttft_hyperparameters() -> None:
    data = _instance_profile_mapping()
    fitted = data["instances"]["latency_profiles"]["instance-a-ttft"]["fitted_ttft"]
    fitted["ms_per_uncached_token"] = -1.0

    with pytest.raises(ValueError, match="ms_per_uncached_token"):
        InstanceProfile.from_mapping(data)


def test_instance_profile_defaults_calibration_window_to_500() -> None:
    data = _instance_profile_mapping()
    fitted = data["instances"]["latency_profiles"]["instance-a-ttft"]["fitted_ttft"]
    del fitted["calibration_window_requests"]

    profile = InstanceProfile.from_mapping(data)

    assert (
        profile.latency_profile_by_name["instance-a-ttft"].fitted_ttft.calibration_window_requests
        == 500
    )


def test_instance_latency_profile_defaults_kv_load_to_zero() -> None:
    data = _instance_profile_mapping()
    del data["instances"]["latency_profiles"]["instance-a-ttft"]["kv_load"]

    profile = InstanceProfile.from_mapping(data)

    kv_load = profile.latency_profile_by_name["instance-a-ttft"].kv_load
    assert kv_load.ddr_ms_per_cached_token == 0.0
    assert kv_load.remote_ms_per_cached_token == 0.0


def test_instance_latency_profile_rejects_invalid_kv_load_hyperparameters() -> None:
    data = _instance_profile_mapping()
    kv_load = data["instances"]["latency_profiles"]["instance-a-ttft"]["kv_load"]
    kv_load["remote_ms_per_cached_token"] = -0.1

    with pytest.raises(ValueError, match="remote_ms_per_cached_token"):
        InstanceProfile.from_mapping(data)


def _instance_profile_mapping() -> dict[str, object]:
    return {
        "instances": {
            "name": "local-fixed-route-latency-example",
            "latency_profiles": {
                "instance-a-ttft": {
                    "backend": "fitted_ttft",
                    "model_name": "glm-v5.1",
                    "hardware_name": "ascend-a3-fast",
                    "fitted_ttft": {
                        "profile": "instance-a-ttft",
                        "function": "token_linear_v1",
                        "intercept_ms": 0.0,
                        "ms_per_uncached_token": 0.010,
                        "calibrated_from": "synthetic",
                        "calibration_window_requests": 500,
                    },
                    "kv_load": {
                        "ddr_ms_per_cached_token": 0.001,
                        "remote_ms_per_cached_token": 0.003,
                    },
                },
                "instance-b-ttft": {
                    "backend": "fitted_ttft",
                    "model_name": "glm-v5.1",
                    "hardware_name": "ascend-a3-slow",
                    "fitted_ttft": {
                        "profile": "instance-b-ttft",
                        "function": "token_linear_v1",
                        "intercept_ms": 0.0,
                        "ms_per_uncached_token": 0.020,
                        "calibrated_from": "synthetic",
                        "calibration_window_requests": 500,
                    },
                    "kv_load": {
                        "ddr_ms_per_cached_token": 0.0,
                        "remote_ms_per_cached_token": 0.0,
                    },
                },
            },
            "items": {
                "instance-a": {
                    "model_name": "glm-v5.1",
                    "deployment": "glm-v5.1-vllm-ascend-prefill",
                    "latency_profile": "instance-a-ttft",
                },
                "instance-b": {
                    "model_name": "glm-v5.1",
                    "deployment": "glm-v5.1-vllm-ascend-prefill",
                    "latency_profile": "instance-b-ttft",
                },
            },
        }
    }
