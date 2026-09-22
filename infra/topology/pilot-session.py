#!/usr/bin/env python3
"""Start an approved GPU pilot attempt with an independent teardown deadline."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid


HERE = Path(__file__).resolve().parent
TERRAFORM_DIR = HERE / "terraform"
RTX_TERRAFORM_DIR = HERE / "terraform-rtx"
RDMA_TERRAFORM_DIR = HERE / "terraform-rdma"
TEARDOWN = HERE / "teardown.sh"
STATE_ROOT = Path.home() / ".codex/run-state/router-h100-pilot"
PLACEMENT_TIMEOUT_SECONDS = 30 * 60
CLEANUP_START_SECONDS = 90 * 60
DELETION_TARGET_SECONDS = 120 * 60
AUTHORIZATION_SCOPE = "h100-pilot-2026-09-16"
FULLY_RUNNING_HOURLY_USD = Decimal("19.80282")
CLEANUP_RESERVE_USD = Decimal("5")
ATTEMPT_ADMISSION_USD = FULLY_RUNNING_HOURLY_USD * Decimal("2") + CLEANUP_RESERVE_USD
FULL_TOPOLOGY_PROFILE = "full-topology"
IPC_DIAGNOSTIC_PROFILE = "ipc-diagnostic"
H200_EVALUATION_PROFILE = "h200-evaluation"
H200_ON_DEMAND_PROFILE = "h200-on-demand"
RTX_ON_DEMAND_PROFILE = "rtx-on-demand"
FULL_TOPOLOGY_CREATES = {
    "nebius_compute_v1_gpu_cluster.local",
    "nebius_mk8s_v1_cluster.topology",
    "nebius_mk8s_v1_node_group.local",
    "nebius_mk8s_v1_node_group.cpu[0]",
    "nebius_mk8s_v1_node_group.remote[0]",
}
RTX_TOPOLOGY_CREATES = FULL_TOPOLOGY_CREATES - {"nebius_compute_v1_gpu_cluster.local"}
RTX_PROJECT_ID = "project-e05tg6xqln007kjqm4t3rs"
RTX_SUBNET_ID = "vpcsubnet-e05tskd8ywwvmzhed8"
PROFILE_POLICIES = {
    "nvlink-rdma-h200-paired": {
        "placement_timeout_seconds": 20 * 60,
        "cleanup_start_seconds": 50 * 60,
        "deletion_target_seconds": 65 * 60,
        "hourly_rate_usd_pretax": Decimal("39.7"),
        "attempt_admission_usd_pretax": Decimal("46"),
        "expected_creates": FULL_TOPOLOGY_CREATES | {
            "nebius_iam_v1_service_account.guard[0]", "nebius_iam_v1_group.guard[0]",
            "nebius_iam_v1_group_membership.guard[0]", "nebius_iam_v1_access_permit.guard[0]"},
        "remote_gpu_preset": "8gpu-128vcpu-1600gb", "remote_on_fabric": True,
        "gpu_platform": "H200", "terraform_gpu_platform": "gpu-h200-sxm",
        "infiniband_fabric": "us-central1-a", "allowed_fabrics": ("us-central1-a",),
        "diagnostic_only": False, "gpu_preemptible": True,
        "purchase_type": "preemptible", "terraform_dir": RDMA_TERRAFORM_DIR,
        "require_project_binding": True,
    },
    "rdma-h200-serving-retry": {
        "placement_timeout_seconds": 20 * 60,
        "cleanup_start_seconds": 27 * 60,
        "deletion_target_seconds": 33 * 60,
        "hourly_rate_usd_pretax": Decimal("39.7"),
        "attempt_admission_usd_pretax": Decimal("21.9"),
        "expected_creates": FULL_TOPOLOGY_CREATES,
        "remote_gpu_preset": "8gpu-128vcpu-1600gb", "remote_on_fabric": True,
        "gpu_platform": "H200", "terraform_gpu_platform": "gpu-h200-sxm",
        "infiniband_fabric": "us-central1-a", "allowed_fabrics": ("us-central1-a",),
        "diagnostic_only": False, "gpu_preemptible": True,
        "purchase_type": "preemptible", "terraform_dir": RDMA_TERRAFORM_DIR,
        "require_project_binding": True,
    },
    "rdma-h200-serving": {
        "placement_timeout_seconds": 15 * 60,
        "cleanup_start_seconds": 35 * 60,
        "deletion_target_seconds": 48 * 60,
        "hourly_rate_usd_pretax": Decimal("39.7"),
        "attempt_admission_usd_pretax": Decimal("32"),
        "expected_creates": FULL_TOPOLOGY_CREATES,
        "remote_gpu_preset": "8gpu-128vcpu-1600gb", "remote_on_fabric": True,
        "gpu_platform": "H200", "terraform_gpu_platform": "gpu-h200-sxm",
        "infiniband_fabric": "us-central1-a", "allowed_fabrics": ("us-central1-a",),
        "diagnostic_only": False, "gpu_preemptible": True,
        "purchase_type": "preemptible", "terraform_dir": RDMA_TERRAFORM_DIR,
        "require_project_binding": True,
    },
    "nvlink-h200-local": {
        "placement_timeout_seconds": 20 * 60,
        "cleanup_start_seconds": 60 * 60,
        "deletion_target_seconds": 75 * 60,
        "hourly_rate_usd_pretax": Decimal("19.625"),
        "attempt_admission_usd_pretax": Decimal("25"),
        "expected_creates": {"nebius_compute_v1_gpu_cluster.local",
                             "nebius_mk8s_v1_cluster.topology",
                             "nebius_mk8s_v1_node_group.local"},
        "gpu_platform": "H200", "terraform_gpu_platform": "gpu-h200-sxm",
        "infiniband_fabric": "us-central1-a", "allowed_fabrics": ("us-central1-a",),
        "diagnostic_only": True, "gpu_preemptible": True,
        "purchase_type": "preemptible", "terraform_dir": RDMA_TERRAFORM_DIR,
        "require_project_binding": True,
    },
    "rdma-h200-local": {
        "placement_timeout_seconds": 20 * 60,
        "cleanup_start_seconds": 90 * 60,
        "deletion_target_seconds": 105 * 60,
        "hourly_rate_usd_pretax": Decimal("19.625"),
        "attempt_admission_usd_pretax": Decimal("36"),
        "expected_creates": {"nebius_compute_v1_gpu_cluster.local",
                             "nebius_mk8s_v1_cluster.topology",
                             "nebius_mk8s_v1_node_group.local"},
        "gpu_platform": "H200", "terraform_gpu_platform": "gpu-h200-sxm",
        "infiniband_fabric": "us-central1-a", "allowed_fabrics": ("us-central1-a",),
        "diagnostic_only": True, "gpu_preemptible": True,
        "purchase_type": "preemptible", "terraform_dir": RDMA_TERRAFORM_DIR,
        "require_project_binding": True,
    },
    FULL_TOPOLOGY_PROFILE: {
        "placement_timeout_seconds": PLACEMENT_TIMEOUT_SECONDS,
        "cleanup_start_seconds": CLEANUP_START_SECONDS,
        "deletion_target_seconds": DELETION_TARGET_SECONDS,
        "hourly_rate_usd_pretax": FULLY_RUNNING_HOURLY_USD,
        "attempt_admission_usd_pretax": ATTEMPT_ADMISSION_USD,
        "expected_creates": FULL_TOPOLOGY_CREATES,
        "gpu_platform": "H100",
        "terraform_gpu_platform": "gpu-h100-sxm",
        "infiniband_fabric": "fabric-6",
        "allowed_fabrics": ("fabric-2", "fabric-3", "fabric-4", "fabric-6"),
        "diagnostic_only": False,
        "gpu_preemptible": True,
        "purchase_type": "preemptible",
    },
    IPC_DIAGNOSTIC_PROFILE: {
        "placement_timeout_seconds": 15 * 60,
        "cleanup_start_seconds": 30 * 60,
        "deletion_target_seconds": 40 * 60,
        "hourly_rate_usd_pretax": Decimal("17.225"),
        "attempt_admission_usd_pretax": Decimal("13"),
        "expected_creates": {
            "nebius_compute_v1_gpu_cluster.local",
            "nebius_mk8s_v1_cluster.topology",
            "nebius_mk8s_v1_node_group.local",
        },
        "gpu_platform": "H100",
        "terraform_gpu_platform": "gpu-h100-sxm",
        "infiniband_fabric": "fabric-6",
        "allowed_fabrics": ("fabric-2", "fabric-3", "fabric-4", "fabric-6"),
        "diagnostic_only": True,
        "gpu_preemptible": True,
        "purchase_type": "preemptible",
    },
    H200_EVALUATION_PROFILE: {
        "placement_timeout_seconds": 30 * 60,
        "first_measurement_timeout_seconds": 90 * 60,
        "cleanup_start_seconds": 270 * 60,
        "deletion_target_seconds": 300 * 60,
        "hourly_rate_usd_pretax": Decimal("22.503"),
        "attempt_admission_usd_pretax": Decimal("120"),
        "expected_creates": FULL_TOPOLOGY_CREATES,
        "gpu_platform": "H200",
        "terraform_gpu_platform": "gpu-h200-sxm",
        "infiniband_fabric": "fabric-7",
        "allowed_fabrics": ("fabric-7",),
        "diagnostic_only": False,
        "gpu_preemptible": True,
        "purchase_type": "preemptible",
    },
    H200_ON_DEMAND_PROFILE: {
        "placement_timeout_seconds": 30 * 60,
        "first_measurement_timeout_seconds": 90 * 60,
        "cleanup_start_seconds": 180 * 60,
        "deletion_target_seconds": 210 * 60,
        "hourly_rate_usd_pretax": Decimal("40.953"),
        "attempt_admission_usd_pretax": Decimal("155"),
        "expected_creates": FULL_TOPOLOGY_CREATES,
        "gpu_platform": "H200",
        "terraform_gpu_platform": "gpu-h200-sxm",
        "infiniband_fabric": "fabric-7",
        "allowed_fabrics": ("fabric-7",),
        "diagnostic_only": False,
        "gpu_preemptible": False,
        "purchase_type": "on-demand",
    },
    RTX_ON_DEMAND_PROFILE: {
        "placement_timeout_seconds": 30 * 60,
        "first_measurement_timeout_seconds": 90 * 60,
        "cleanup_start_seconds": 270 * 60,
        "deletion_target_seconds": 300 * 60,
        "hourly_rate_usd_pretax": Decimal("16.653"),
        "attempt_admission_usd_pretax": Decimal("100"),
        "expected_creates": RTX_TOPOLOGY_CREATES,
        "gpu_platform": "RTX6000-A",
        "terraform_gpu_platform": "gpu-rtx6000-a",
        "local_gpu_preset": "8gpu-192vcpu-1744gb",
        "remote_gpu_preset": "1gpu-24vcpu-218gb",
        "infiniband_fabric": "",
        "allowed_fabrics": ("",),
        "diagnostic_only": False,
        "gpu_preemptible": False,
        "purchase_type": "on-demand",
        "terraform_dir": RTX_TERRAFORM_DIR,
        "project_id": RTX_PROJECT_ID,
        "subnet_id": RTX_SUBNET_ID,
        "uses_gpu_cluster": False,
        "attention_backend": "TRITON_ATTN",
    },
}


PAIRED_PROFILES = ("nvlink-rdma-h200-paired", "nvlink-rdma-h200-paired-short", "tp4-h200-staged", "tp4-h200-staged-retry", "tp4-h200-health-retry")
PROFILE_POLICIES[PAIRED_PROFILES[1]] = {
    **PROFILE_POLICIES[PAIRED_PROFILES[0]],
    "cleanup_start_seconds": 40 * 60,
    "deletion_target_seconds": 55 * 60,
    "attempt_admission_usd_pretax": Decimal("38"),
}
PROFILE_POLICIES[PAIRED_PROFILES[2]] = {
    **PROFILE_POLICIES[PAIRED_PROFILES[0]],
    "cleanup_start_seconds": 120 * 60,
    "deletion_target_seconds": 145 * 60,
    "attempt_admission_usd_pretax": Decimal("100"),
}
PROFILE_POLICIES[PAIRED_PROFILES[3]] = {
    **PROFILE_POLICIES[PAIRED_PROFILES[0]],
    "cleanup_start_seconds": 100 * 60,
    "deletion_target_seconds": 120 * 60,
    "attempt_admission_usd_pretax": Decimal("82"),
}
PROFILE_POLICIES[PAIRED_PROFILES[4]] = {
    **PROFILE_POLICIES[PAIRED_PROFILES[0]],
    "cleanup_start_seconds": 80 * 60,
    "deletion_target_seconds": 100 * 60,
    "attempt_admission_usd_pretax": Decimal("67"),
}


LAYOUT_PROFILE = "tp4-h200-layout-local"
LAYOUT_GUARD_ID = "serviceaccount-u00z5fq630hbbrd7ck"
PROFILE_POLICIES[LAYOUT_PROFILE] = {
    **PROFILE_POLICIES[PAIRED_PROFILES[0]],
    # Bounded overnight retry: same four epochs, less idle/setup headroom.
    "cleanup_start_seconds": 75 * 60,
    "deletion_target_seconds": 95 * 60,
    "hourly_rate_usd_pretax": Decimal("20.05"),
    "attempt_admission_usd_pretax": Decimal("33"),
    "expected_creates": FULL_TOPOLOGY_CREATES - {"nebius_mk8s_v1_node_group.remote[0]"},
    "single_gpu_host": True,
    "existing_guard_service_account_id": LAYOUT_GUARD_ID,
    "project_id": "project-u00k8gmbpr0067akfrxdah",
    "subnet_id": "vpcsubnet-u00fjg7m2r4s10h9sb",
}
GUARDED_PROFILES = (*PAIRED_PROFILES, LAYOUT_PROFILE)


class SessionError(RuntimeError):
    pass


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise SessionError(f"Expected a JSON object in {path}")
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def positive_money(value) -> Decimal:
    try:
        money = Decimal(str(value))
    except InvalidOperation as error:
        raise SessionError("Approved budget must be a number") from error
    if not money.is_finite() or money <= 0:
        raise SessionError("Approved budget must be positive and finite")
    return money


def nonnegative_money(value, label: str) -> Decimal:
    try:
        money = Decimal(str(value))
    except InvalidOperation as error:
        raise SessionError(f"{label} must be a number") from error
    if not money.is_finite() or money < 0:
        raise SessionError(f"{label} must be non-negative and finite")
    return money


def authorization_expiry_unix(value) -> float | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise SessionError("authorization_expires_at must be a UTC ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SessionError("authorization_expires_at must be a UTC ISO 8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise SessionError("authorization_expires_at must be a UTC ISO 8601 timestamp")
    return parsed.timestamp()


def profile_policy(profile: str) -> dict:
    try:
        return PROFILE_POLICIES[profile]
    except KeyError as error:
        raise SessionError(f"Unknown pilot profile: {profile}") from error


def profile_terraform_dir(profile: str) -> Path:
    return Path(profile_policy(profile).get("terraform_dir", TERRAFORM_DIR)).resolve()


def session_profile(session: dict) -> str:
    """Old session records predate profiles and retain full-topology semantics."""
    return session.get("profile", FULL_TOPOLOGY_PROFILE)


def session_hourly_rate(session: dict) -> Decimal:
    policy = profile_policy(session_profile(session))
    expected = policy["hourly_rate_usd_pretax"]
    if "hourly_rate_usd_pretax" not in session:
        return expected
    recorded = positive_money(session["hourly_rate_usd_pretax"])
    if recorded != expected:
        raise SessionError("Session hourly rate does not match its profile")
    return recorded


def session_terraform_settings(session: dict) -> tuple[str, str, bool, bool]:
    policy = profile_policy(session_profile(session))
    platform = session.get("gpu_platform", policy["gpu_platform"])
    fabric = session.get("infiniband_fabric", policy["infiniband_fabric"])
    diagnostic = session.get("ipc_diagnostic_only", policy["diagnostic_only"])
    preemptible = session.get("gpu_preemptible", policy["gpu_preemptible"])
    if (platform != policy["gpu_platform"] or fabric not in policy["allowed_fabrics"]
            or diagnostic is not policy["diagnostic_only"]
            or preemptible is not policy["gpu_preemptible"]):
        raise SessionError("Session Terraform settings do not match its profile")
    return platform, fabric, diagnostic, preemptible


def session_infrastructure_settings(session: dict) -> tuple[Path, str | None, str | None]:
    policy = profile_policy(session_profile(session))
    expected_dir = profile_terraform_dir(session_profile(session))
    if policy.get("project_id") is not None or policy.get("require_project_binding"):
        missing = [key for key in ("terraform_dir", "project_id", "subnet_id") if key not in session]
        if missing:
            raise SessionError("Session lacks pinned RTX infrastructure settings: " + ", ".join(missing))
        if not all(session.get(key) for key in ("terraform_dir", "project_id", "subnet_id")):
            raise SessionError("Session has empty infrastructure settings")
    recorded_dir = Path(session.get("terraform_dir", expected_dir)).resolve()
    if recorded_dir != expected_dir:
        raise SessionError("Session Terraform directory does not match its profile")
    expected_project = policy.get("project_id")
    expected_subnet = policy.get("subnet_id")
    project = session.get("project_id", expected_project)
    subnet = session.get("subnet_id", expected_subnet)
    if expected_project is not None and project != expected_project:
        raise SessionError("Session project does not match its profile")
    if expected_subnet is not None and subnet != expected_subnet:
        raise SessionError("Session subnet does not match its profile")
    return recorded_dir, project, subnet


def singleton_object(value, label: str) -> dict:
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    if not isinstance(value, dict):
        raise SessionError(f"Terraform plan has invalid {label}")
    return value


def plan_boolean(value, label: str) -> bool:
    if value is True or value == "true":
        return True
    if value is False or value == "false":
        return False
    raise SessionError(f"Terraform plan has invalid {label}")


def validate_plan_structure(plan: dict, profile: str) -> dict:
    policy = profile_policy(profile)
    changes = plan.get("resource_changes")
    if not isinstance(changes, list):
        raise SessionError("Terraform plan JSON has no resource_changes list")

    material = changes
    invalid = [
        change.get("address", "<unknown>")
        for change in material
        if change.get("change", {}).get("actions") != ["create"]
    ]
    if invalid:
        raise SessionError(f"Terraform plan contains non-create changes: {', '.join(invalid)}")

    actual = {change.get("address") for change in material}
    expected = policy["expected_creates"]
    if actual != expected or len(material) != len(expected):
        raise SessionError(
            f"Terraform plan does not match profile {profile}: expected exactly "
            f"{len(expected)} creates ({', '.join(sorted(expected))}); got "
            f"{len(material)} ({', '.join(sorted(str(item) for item in actual))})"
        )

    variables = plan.get("variables", {})
    project_id = variables.get("project_id", {}).get("value")
    subnet_id = variables.get("subnet_id", {}).get("value")
    if policy.get("project_id") is not None and project_id != policy["project_id"]:
        raise SessionError(f"Terraform plan project_id does not match profile {profile}")
    if policy.get("subnet_id") is not None and subnet_id != policy["subnet_id"]:
        raise SessionError(f"Terraform plan subnet_id does not match profile {profile}")
    diagnostic_value = plan_boolean(
        variables.get("ipc_diagnostic_only", {}).get("value"), "ipc_diagnostic_only"
    )
    if diagnostic_value is not policy["diagnostic_only"]:
        raise SessionError(f"Terraform plan ipc_diagnostic_only does not match profile {profile}")
    preemptible = plan_boolean(
        variables.get("gpu_preemptible", {}).get("value"), "gpu_preemptible"
    )
    if preemptible is not policy["gpu_preemptible"]:
        capacity = "preemptible" if policy["gpu_preemptible"] else "on-demand"
        raise SessionError(f"Terraform plan must use {capacity} GPU capacity")

    gpu_platform = variables.get("gpu_platform", {}).get("value")
    if gpu_platform != policy["gpu_platform"]:
        raise SessionError(f"Terraform plan gpu_platform does not match profile {profile}")
    infiniband_fabric = variables.get("infiniband_fabric", {}).get("value")
    if infiniband_fabric not in policy["allowed_fabrics"]:
        raise SessionError(f"Terraform plan infiniband_fabric does not match profile {profile}")

    if policy.get("uses_gpu_cluster", True):
        gpu_cluster_change = next(
            change for change in material
            if change.get("address") == "nebius_compute_v1_gpu_cluster.local"
        )
        gpu_cluster = singleton_object(
            gpu_cluster_change.get("change", {}).get("after"), "local GPU cluster"
        )
        if gpu_cluster.get("infiniband_fabric") != infiniband_fabric:
            raise SessionError("Terraform plan local GPU cluster uses the wrong InfiniBand fabric")

    local_change = next(
        change for change in material if change.get("address") == "nebius_mk8s_v1_node_group.local"
    )
    after = singleton_object(local_change.get("change", {}).get("after"), "local node group")
    template = singleton_object(after.get("template"), "local node template")
    resources = singleton_object(template.get("resources"), "local node resources")
    boot_disk = singleton_object(template.get("boot_disk"), "local node boot disk")
    if after.get("fixed_node_count") != 1:
        raise SessionError("Terraform plan must create exactly one local node")
    if (
        resources.get("platform") != policy["terraform_gpu_platform"]
        or resources.get("preset") != policy.get("local_gpu_preset", "8gpu-128vcpu-1600gb")
    ):
        raise SessionError(
            "Terraform plan local node must use "
            f"{policy['terraform_gpu_platform']} "
            f"{policy.get('local_gpu_preset', '8gpu-128vcpu-1600gb')}"
        )
    if not policy.get("uses_gpu_cluster", True) and template.get("gpu_cluster") is not None:
        raise SessionError("Terraform plan local RTX node must not use a GPU cluster")
    expected_preemptible = {} if policy["gpu_preemptible"] else None
    if template.get("preemptible") != expected_preemptible:
        raise SessionError("Terraform plan local node GPU purchase type does not match its profile")
    local_reservation = template.get("reservation_policy")
    valid_local_reservation = local_reservation is None
    if not policy["gpu_preemptible"]:
        valid_local_reservation = (
            isinstance(local_reservation, dict)
            and local_reservation.get("policy") == "FORBID"
            and local_reservation.get("reservation_ids") in (None, [])
        )
    if not valid_local_reservation:
        raise SessionError("Terraform plan local node reservation policy does not match its profile")
    if boot_disk.get("type") != "NETWORK_SSD" or boot_disk.get("size_gibibytes") != 256:
        raise SessionError("Terraform plan local node boot disk must be a 256 GiB NETWORK_SSD")

    if not policy["diagnostic_only"] and not policy.get("single_gpu_host"):
        remote_change = next(
            change
            for change in material
            if change.get("address") == "nebius_mk8s_v1_node_group.remote[0]"
        )
        remote = singleton_object(remote_change.get("change", {}).get("after"), "remote node group")
        remote_template = singleton_object(remote.get("template"), "remote node template")
        remote_resources = singleton_object(remote_template.get("resources"), "remote node resources")
        remote_disk = singleton_object(remote_template.get("boot_disk"), "remote node boot disk")
        if remote.get("fixed_node_count") != 1:
            raise SessionError("Terraform plan must create exactly one remote node")
        if (
            remote_resources.get("platform") != policy["terraform_gpu_platform"]
            or remote_resources.get("preset") != policy.get("remote_gpu_preset", "1gpu-16vcpu-200gb")
        ):
            raise SessionError(
                "Terraform plan remote node must use "
                f"{policy['terraform_gpu_platform']} "
                f"{policy.get('remote_gpu_preset', '1gpu-16vcpu-200gb')}"
            )
        if remote_template.get("preemptible") != expected_preemptible:
            raise SessionError("Terraform plan remote node GPU purchase type does not match its profile")
        remote_reservation = remote_template.get("reservation_policy")
        valid_remote_reservation = remote_reservation is None
        if not policy["gpu_preemptible"]:
            valid_remote_reservation = (
                isinstance(remote_reservation, dict)
                and remote_reservation.get("policy") == "FORBID"
                and remote_reservation.get("reservation_ids") in (None, [])
            )
        if not valid_remote_reservation:
            raise SessionError("Terraform plan remote node reservation policy does not match its profile")
        if policy.get("remote_on_fabric"):
            refs = plan.get("configuration", {}).get("root_module", {}).get("resources", [])
            remote_config = next((r for r in refs if r.get("address") == "nebius_mk8s_v1_node_group.remote"), {})
            if "nebius_compute_v1_gpu_cluster.local.id" not in json.dumps(remote_config):
                raise SessionError("Remote node must reference the same GPU cluster")
        if not policy.get("remote_on_fabric") and remote_template.get("gpu_cluster") is not None:
            raise SessionError("Terraform plan remote node must remain outside the local GPU cluster")
        if remote_disk.get("type") != "NETWORK_SSD" or remote_disk.get("size_gibibytes") != 256:
            raise SessionError("Terraform plan remote node boot disk must be a 256 GiB NETWORK_SSD")

    if not policy["diagnostic_only"]:
        cpu_change = next(
            change
            for change in material
            if change.get("address") == "nebius_mk8s_v1_node_group.cpu[0]"
        )
        cpu = singleton_object(cpu_change.get("change", {}).get("after"), "CPU node group")
        cpu_template = singleton_object(cpu.get("template"), "CPU node template")
        cpu_resources = singleton_object(cpu_template.get("resources"), "CPU node resources")
        cpu_disk = singleton_object(cpu_template.get("boot_disk"), "CPU node boot disk")
        if cpu.get("fixed_node_count") != 1:
            raise SessionError("Terraform plan must create exactly one CPU node")
        if cpu_resources.get("platform") != "cpu-d3" or cpu_resources.get("preset") != "16vcpu-64gb":
            raise SessionError("Terraform plan CPU node must use cpu-d3 16vcpu-64gb")
        if cpu_template.get("preemptible") is not None:
            raise SessionError("Terraform plan CPU node must use standard capacity")
        if cpu_disk.get("type") != "NETWORK_SSD" or cpu_disk.get("size_gibibytes") != 64:
            raise SessionError("Terraform plan CPU node boot disk must be a 64 GiB NETWORK_SSD")

    if profile in GUARDED_PROFILES:
        if not plan_boolean(variables.get("cloud_guard", {}).get("value"), "cloud_guard"):
            raise SessionError("Paired overnight run requires the cloud guard")
        if policy.get("existing_guard_service_account_id"):
            if variables.get("existing_guard_service_account_id", {}).get("value") != policy["existing_guard_service_account_id"]:
                raise SessionError("Wrong reusable cleanup identity")
            if cpu_template.get("service_account_id") != policy["existing_guard_service_account_id"]:
                raise SessionError("CPU guard must use the reviewed cleanup identity")
            if not plan_boolean(variables.get("single_gpu_host", {}).get("value"), "single_gpu_host"):
                raise SessionError("Layout diagnostic must omit the remote GPU host")
        else:
            permit = next(x["change"]["after"] for x in changes if x["address"] == "nebius_iam_v1_access_permit.guard[0]")
            if permit.get("role") != "editor" or permit.get("resource_id") != project_id:
                raise SessionError("Cleanup identity must be scoped to the experiment project")
    return {"gpu_platform": gpu_platform, "infiniband_fabric": infiniband_fabric,
            "ipc_diagnostic_only": diagnostic_value, "gpu_preemptible": preemptible,
            "project_id": project_id, "subnet_id": subnet_id,
            "terraform_dir": str(profile_terraform_dir(profile))}

def validate_terraform_plan(plan_path: Path, profile: str, *, run=subprocess.run) -> dict:
    completed = run(
        ["terraform", f"-chdir={profile_terraform_dir(profile)}", "show", "-json",
         str(plan_path.resolve())],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "terraform show failed"
        raise SessionError(f"Could not inspect Terraform plan: {detail}")
    try:
        plan = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise SessionError("Terraform show returned invalid JSON") from error
    if not isinstance(plan, dict):
        raise SessionError("Terraform show did not return a JSON object")
    return validate_plan_structure(plan, profile)


def cleanup_verified(run_dir: Path, session_id: str) -> bool:
    marker = run_dir / "cleanup-verified.json"
    try:
        value = read_json(marker)
    except (FileNotFoundError, json.JSONDecodeError, OSError, SessionError):
        return False
    return value.get("session_id") == session_id and value.get("teardown_exit_code") == 0


def load_or_initialize_budget(state_root: Path, approval: dict, approved_budget: Decimal) -> dict:
    budget_path = state_root / "budget.json"
    historical_spend = nonnegative_money(
        approval.get("historical_spend_usd_pretax"), "Historical spend"
    )
    if budget_path.exists():
        budget = read_json(budget_path)
        if budget.get("authorization_scope") != AUTHORIZATION_SCOPE:
            raise SessionError("Budget ledger authorization scope does not match this pilot")
        recorded_budget = positive_money(budget.get("approved_max_total_usd_pretax"))
        if approved_budget < recorded_budget:
            if approval.get("replace_remaining_budget") is not True:
                raise SessionError("Approval record cannot reduce the existing cumulative budget")
            if approved_budget < completed_spend(budget):
                raise SessionError("Replacement budget is below completed spending")
        if approved_budget != recorded_budget:
            budget["approved_max_total_usd_pretax"] = str(approved_budget)
            budget.setdefault("authorization_updates", []).append({
                "approval_reference": approval["approval_reference"],
                "approved_max_total_usd_pretax": str(approved_budget),
            })
            write_json(budget_path, budget)
        return budget

    attempts = []
    legacy_path = state_root / "attempt.json"
    if legacy_path.exists():
        legacy = read_json(legacy_path)
        run_dir = Path(legacy.get("run_dir", ""))
        session_id = legacy.get("session_id")
        if not session_id or not cleanup_verified(run_dir, session_id):
            raise SessionError("Legacy attempt cleanup is not verified; refusing another attempt")
        cost_path = run_dir / "cost-estimate.json"
        if not cost_path.is_file():
            raise SessionError("Legacy attempt has no recorded cost estimate; refusing another attempt")
        recorded_cost = nonnegative_money(
            read_json(cost_path).get("estimate_usd_pretax"), "Legacy attempt cost"
        )
        if recorded_cost != historical_spend:
            raise SessionError("Approval historical spend does not match the legacy cost estimate")
        attempts.append({
            "session_id": session_id,
            "run_dir": str(run_dir),
            "status": "completed",
            "estimated_cost_usd_pretax": str(recorded_cost),
            "legacy_attempt_path": str(legacy_path),
        })
    elif historical_spend != 0:
        raise SessionError("Approval records historical spend but no legacy attempt exists")

    budget = {
        "authorization_scope": AUTHORIZATION_SCOPE,
        "approved_max_total_usd_pretax": str(approved_budget),
        "historical_spend_usd_pretax": str(historical_spend),
        "attempts": attempts,
    }
    write_json(budget_path, budget)
    return budget


def completed_spend(budget: dict) -> Decimal:
    total = Decimal("0")
    for attempt in budget.get("attempts", []):
        if attempt.get("status") != "completed":
            raise SessionError(
                f"Attempt {attempt.get('session_id', '<unknown>')} is still active; overlapping launch rejected"
            )
        run_dir = Path(attempt.get("run_dir", ""))
        session_id = attempt.get("session_id")
        if not session_id or not cleanup_verified(run_dir, session_id):
            raise SessionError("Prior attempt cleanup is not verified; refusing another attempt")
        if "estimated_cost_usd_pretax" not in attempt:
            raise SessionError("Prior attempt has no recorded cost estimate; refusing another attempt")
        total += nonnegative_money(attempt["estimated_cost_usd_pretax"], "Prior attempt cost")
    return total


def validated_attempt_cost(run_dir: Path, session: dict, verified_unix: float) -> Decimal:
    cost_path = run_dir / "cost-estimate.json"
    if not cost_path.is_file():
        raise SessionError("Prior attempt has no recorded cost estimate; refusing another attempt")
    cost_record = read_json(cost_path)
    if cost_record.get("session_id") != session["session_id"]:
        raise SessionError("Attempt cost estimate does not match its session_id")
    estimated_cost = nonnegative_money(cost_record.get("estimate_usd_pretax"), "Attempt cost")
    elapsed = max(Decimal("0"), Decimal(str(verified_unix)) - Decimal(str(session["started_unix"])))
    conservative_upper = elapsed * session_hourly_rate(session) / Decimal("3600") + Decimal("0.01")
    if estimated_cost > conservative_upper:
        raise SessionError("Attempt cost estimate exceeds the full-rate session upper bound")
    return estimated_cost


def mark_attempt_cleanup_verified(run_dir: Path, verified_unix: float) -> None:
    session = read_json(run_dir / "session.json")
    state_root = Path(session["state_root"])
    attempt_path = run_dir / "attempt.json"
    with (state_root / "budget.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        budget = read_json(state_root / "budget.json")
        matches = [item for item in budget.get("attempts", []) if item.get("session_id") == session["session_id"]]
        if len(matches) != 1:
            raise SessionError("Completed attempt is missing from the budget ledger")
        attempt = matches[0]
        if attempt.get("status") == "completed":
            return
        attempt.update({
            "status": "cleanup_verified_cost_pending",
            "cleanup_verified_unix": verified_unix,
        })
        write_json(attempt_path, attempt)
        write_json(state_root / "budget.json", budget)


def reconcile_recorded_costs(budget: dict) -> None:
    for attempt in budget.get("attempts", []):
        if attempt.get("status") != "cleanup_verified_cost_pending":
            continue
        run_dir = Path(attempt["run_dir"])
        session = read_json(run_dir / "session.json")
        verified_unix = attempt["cleanup_verified_unix"]
        estimated_cost = validated_attempt_cost(run_dir, session, verified_unix)
        attempt.update({
            "status": "completed",
            "estimated_cost_usd_pretax": str(estimated_cost),
            "cost_record_path": str(run_dir / "cost-estimate.json"),
        })
        write_json(run_dir / "attempt.json", attempt)


def prepare_session(args, state_root: Path = STATE_ROOT, now: float | None = None) -> tuple[Path, dict]:
    if (state_root / "manual-stop.json").exists():
        raise SessionError("User emergency stop is active; explicit user resume is required before clearing it")
    if not args.execute:
        raise SessionError("Refusing to spend: --execute is required")
    if not args.plan.is_file():
        raise SessionError(f"Terraform plan does not exist: {args.plan}")
    if not args.approval_record.is_file():
        raise SessionError(f"Approval record does not exist: {args.approval_record}")

    profile = getattr(args, "profile", FULL_TOPOLOGY_PROFILE)
    policy = profile_policy(profile)
    approval = read_json(args.approval_record)
    if approval.get("approved") is not True:
        raise SessionError("Approval record must contain approved: true")
    if not approval.get("approval_reference"):
        raise SessionError("Approval record must contain a non-empty approval_reference")
    if approval.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise SessionError(f"Approval authorization_scope must be {AUTHORIZATION_SCOPE}")
    if approval.get("allow_multiple_attempts") is not True:
        raise SessionError("Approval must explicitly allow multiple attempts")
    approved_budget = positive_money(approval.get("max_total_usd_pretax"))
    cli_budget = positive_money(args.approved_max_usd_pretax)
    if approved_budget != cli_budget:
        raise SessionError("CLI budget does not match the approval record")
    if approval.get("purchase_type") != args.purchase_type:
        raise SessionError("CLI purchase type does not match the approval record")
    if args.purchase_type != policy["purchase_type"]:
        raise SessionError(f"Profile {profile} requires purchase type {policy['purchase_type']}")
    allowed_profiles = approval.get("allowed_profiles", [FULL_TOPOLOGY_PROFILE])
    if not isinstance(allowed_profiles, list) or profile not in allowed_profiles:
        raise SessionError(f"Approval record does not allow profile {profile}")
    plan_settings = validate_terraform_plan(args.plan, profile)
    if policy.get("require_project_binding"):
        for key in ("project_id", "subnet_id"):
            if not approval.get(key) or approval[key] != plan_settings[key]:
                raise SessionError("RDMA plan differs from its approved " + key)
    started = time.time() if now is None else now
    authorization_expiry = authorization_expiry_unix(approval.get("authorization_expires_at"))
    if (
        authorization_expiry is not None
        and started + policy["deletion_target_seconds"] > authorization_expiry
    ):
        raise SessionError("Session deletion target would exceed authorization_expires_at")

    state_root.mkdir(parents=True, exist_ok=True)
    with (state_root / "budget.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        budget = load_or_initialize_budget(state_root, approval, approved_budget)
        reconcile_recorded_costs(budget)
        spent = completed_spend(budget)
        remaining = approved_budget - spent
        budget["completed_spend_usd_pretax"] = str(spent)
        budget["remaining_usd_pretax"] = str(remaining)
        write_json(state_root / "budget.json", budget)
        attempt_admission = policy["attempt_admission_usd_pretax"]
        if remaining < attempt_admission:
            raise SessionError(
                f"Remaining budget ${remaining} is below the ${attempt_admission} attempt admission"
            )
        session_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(started)) + "-" + uuid.uuid4().hex[:8]
        run_dir = state_root / "runs" / session_id
        run_dir.mkdir(parents=True)
        session = {
            "session_id": session_id,
            "started_unix": started,
            "profile": profile,
            "gpu_platform": plan_settings["gpu_platform"],
            "infiniband_fabric": plan_settings["infiniband_fabric"],
            "ipc_diagnostic_only": plan_settings["ipc_diagnostic_only"],
            "gpu_preemptible": plan_settings["gpu_preemptible"],
            "terraform_dir": plan_settings["terraform_dir"],
            "project_id": plan_settings["project_id"],
            "subnet_id": plan_settings["subnet_id"],
            "placement_timeout_seconds": policy["placement_timeout_seconds"],
            "cleanup_start_seconds": policy["cleanup_start_seconds"],
            "deletion_target_seconds": policy["deletion_target_seconds"],
            "placement_deadline_unix": started + policy["placement_timeout_seconds"],
            "cleanup_start_deadline_unix": started + policy["cleanup_start_seconds"],
            "deletion_target_unix": started + policy["deletion_target_seconds"],
            "hourly_rate_usd_pretax": str(policy["hourly_rate_usd_pretax"]),
            "approved_max_usd_pretax": float(cli_budget),
            "purchase_type": args.purchase_type,
            "attention_backend": policy.get("attention_backend"),
            "approval_reference": approval["approval_reference"],
            "approval_record_path": str(args.approval_record.resolve()),
            "approval_record_sha256": file_sha256(args.approval_record),
            "terraform_plan_path": str(args.plan.resolve()),
            "terraform_plan_sha256": file_sha256(args.plan),
            "run_dir": str(run_dir),
            "state_root": str(state_root),
            "budget_snapshot": {
                "authorization_scope": AUTHORIZATION_SCOPE,
                "approved_max_total_usd_pretax": str(approved_budget),
                "completed_spend_usd_pretax": str(spent),
                "remaining_before_attempt_usd_pretax": str(remaining),
                "attempt_admission_usd_pretax": str(attempt_admission),
                "note": "Local admission estimate only; not a provider-side hard cap.",
            },
        }
        if "first_measurement_timeout_seconds" in policy:
            session["first_measurement_timeout_seconds"] = policy[
                "first_measurement_timeout_seconds"
            ]
            session["first_measurement_deadline_unix"] = (
                started + policy["first_measurement_timeout_seconds"]
            )
        if authorization_expiry is not None:
            session["authorization_expires_at"] = approval["authorization_expires_at"]
            session["authorization_expires_unix"] = authorization_expiry
        attempt = {
            "session_id": session_id,
            "created_unix": started,
            "run_dir": str(run_dir),
            "status": "active",
        }
        write_json(run_dir / "session.json", session)
        write_json(run_dir / "attempt.json", attempt)
        budget["attempts"].append(attempt)
        write_json(state_root / "budget.json", budget)
        return run_dir, session


def cleanup_until_target(
    run_dir: Path,
    *,
    now_fn=time.time,
    sleep_fn=time.sleep,
    run_teardown_fn=None,
) -> int:
    session = read_json(run_dir / "session.json")
    if run_teardown_fn is None:
        profile = session_profile(session)
        _, fabric, _, preemptible = session_terraform_settings(session)
        terraform_dir, project_id, subnet_id = session_infrastructure_settings(session)

        def run_teardown_fn(log, timeout):
            return run_teardown_once(
                log,
                timeout,
                profile=profile,
                infiniband_fabric=fabric,
                gpu_preemptible=preemptible,
                terraform_dir=terraform_dir,
                project_id=project_id,
                subnet_id=subnet_id,
            )

    session_id = session["session_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "cleanup.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if cleanup_verified(run_dir, session_id):
            if "state_root" in session:
                mark_attempt_cleanup_verified(
                    run_dir, read_json(run_dir / "cleanup-verified.json")["verified_unix"]
                )
            return 0

        attempt_number = 0
        while True:
            attempt_number += 1
            started = now_fn()
            with (run_dir / "cleanup.log").open("a") as log:
                log.write(f"cleanup attempt {attempt_number} started at {started}\n")
                log.flush()
                remaining_before_target = session["deletion_target_unix"] - now_fn()
                timeout = min(10 * 60, max(1, remaining_before_target)) if remaining_before_target > 0 else 10 * 60
                exit_code = run_teardown_fn(log, timeout)
            write_json(run_dir / f"cleanup-attempt-{attempt_number:02d}.json", {
                "attempt": attempt_number,
                "started_unix": started,
                "finished_unix": now_fn(),
                "teardown_exit_code": exit_code,
            })
            if exit_code == 0:
                (run_dir / "cleanup-overdue.json").unlink(missing_ok=True)
                verified_unix = now_fn()
                write_json(run_dir / "cleanup-verified.json", {
                    "session_id": session_id,
                    "verified_unix": verified_unix,
                    "teardown_exit_code": 0,
                })
                if "state_root" in session:
                    mark_attempt_cleanup_verified(run_dir, verified_unix)
                return 0
            remaining = session["deletion_target_unix"] - now_fn()
            if remaining <= 0:
                write_json(run_dir / "cleanup-overdue.json", {
                    "session_id": session_id,
                    "last_attempt": attempt_number,
                    "last_exit_code": exit_code,
                    "updated_unix": now_fn(),
                    "message": "Deletion is not verified; deletion-only retries continue.",
                })
            sleep_fn(60 if remaining <= 0 else min(60, remaining))


def run_teardown_once(
    log,
    timeout_seconds: float,
    *,
    profile: str = FULL_TOPOLOGY_PROFILE,
    infiniband_fabric: str | None = None,
    gpu_preemptible: bool | None = None,
    terraform_dir: Path | None = None,
    project_id: str | None = None,
    subnet_id: str | None = None,
    popen_factory=subprocess.Popen,
    killpg_fn=os.killpg,
) -> int:
    policy = profile_policy(profile)
    environment = {key: value for key, value in os.environ.items() if key != "NEBIUS_IAM_TOKEN"}
    environment["TF_VAR_cloud_guard"] = "true" if profile in GUARDED_PROFILES else "false"
    environment["TF_VAR_single_gpu_host"] = "true" if policy.get("single_gpu_host") else "false"
    environment["TF_VAR_existing_guard_service_account_id"] = policy.get("existing_guard_service_account_id", "")
    environment["TF_VAR_gpu_platform"] = policy["gpu_platform"]
    fabric = infiniband_fabric or policy["infiniband_fabric"]
    if fabric not in policy["allowed_fabrics"]:
        raise SessionError("Cleanup fabric does not match its profile")
    environment["TF_VAR_infiniband_fabric"] = fabric
    environment["TF_VAR_ipc_diagnostic_only"] = "true" if policy["diagnostic_only"] else "false"
    preemptible = policy["gpu_preemptible"] if gpu_preemptible is None else gpu_preemptible
    if preemptible is not policy["gpu_preemptible"]:
        raise SessionError("Cleanup GPU purchase type does not match its profile")
    environment["TF_VAR_gpu_preemptible"] = "true" if preemptible else "false"
    expected_dir = profile_terraform_dir(profile)
    selected_dir = Path(terraform_dir or expected_dir).resolve()
    if selected_dir != expected_dir:
        raise SessionError("Cleanup Terraform directory does not match its profile")
    environment["ROUTER_TERRAFORM_DIR"] = str(selected_dir)
    expected_project = policy.get("project_id")
    if expected_project is not None and project_id != expected_project:
        raise SessionError("Cleanup project does not match its profile")
    if project_id:
        environment["PROJECT_ID"] = project_id
        environment["TF_VAR_project_id"] = project_id
    expected_subnet = policy.get("subnet_id")
    if expected_subnet is not None and subnet_id != expected_subnet:
        raise SessionError("Cleanup subnet does not match its profile")
    if subnet_id:
        environment["TF_VAR_subnet_id"] = subnet_id
    process = popen_factory(
        ["bash", str(TEARDOWN), "--execute"],
        cwd=HERE,
        stdout=log,
        stderr=subprocess.STDOUT,
        env=environment,
        start_new_session=True,
    )
    try:
        return process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        try:
            killpg_fn(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                killpg_fn(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        return 124


def first_timing_block_complete(run_dir: Path, session_id: str) -> bool:
    try:
        marker = read_json(run_dir / "first-timing-block-complete.json")
    except (FileNotFoundError, json.JSONDecodeError, OSError, SessionError):
        return False
    return marker.get("session_id") == session_id and marker.get("validated") is True


def guard_run(run_dir: Path, *, now_fn=time.time, sleep_fn=time.sleep, cleanup_fn=cleanup_until_target) -> int:
    session = read_json(run_dir / "session.json")
    session_terraform_settings(session)
    write_json(run_dir / "guard-ready.json", {
        "session_id": session["session_id"],
        "pid": os.getpid(),
        "ready_unix": now_fn(),
    })
    while not cleanup_verified(run_dir, session["session_id"]):
        if (run_dir / "manual-stop-requested.json").exists():
            return cleanup_fn(run_dir)
        current = now_fn()
        measurement_deadline = session.get("first_measurement_deadline_unix")
        if (
            measurement_deadline is not None
            and current >= measurement_deadline
            and not first_timing_block_complete(run_dir, session["session_id"])
        ):
            return cleanup_fn(run_dir)
        cleanup_remaining = session["cleanup_start_deadline_unix"] - current
        if cleanup_remaining <= 0:
            return cleanup_fn(run_dir)
        wake_in = cleanup_remaining
        if measurement_deadline is not None and current < measurement_deadline:
            wake_in = min(wake_in, measurement_deadline - current)
        sleep_fn(min(5, wake_in))
    return 0


def spawn_guard(run_dir: Path) -> subprocess.Popen:
    with (run_dir / "guard.log").open("a") as log:
        return subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "guard", str(run_dir)],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )


def wait_guard_ready(run_dir: Path, process: subprocess.Popen) -> None:
    for _ in range(50):
        if (run_dir / "guard-ready.json").exists():
            return
        if process.poll() is not None:
            raise SessionError(f"Deadline guard exited before becoming ready (exit {process.returncode})")
        time.sleep(0.1)
    raise SessionError("Deadline guard did not become ready; Terraform apply was not started")


def apply_plan(
    run_dir: Path,
    session: dict,
    *,
    popen_factory=subprocess.Popen,
    cleanup_fn=cleanup_until_target,
) -> int:
    if (run_dir / "manual-stop-requested.json").exists():
        raise SessionError("User requested this session stop before Terraform apply")
    plan = Path(session["terraform_plan_path"])
    if not plan.is_file() or file_sha256(plan) != session["terraform_plan_sha256"]:
        raise SessionError("Terraform plan is missing or changed since the session was reserved")
    environment = {key: value for key, value in os.environ.items() if key != "NEBIUS_IAM_TOKEN"}
    terraform_dir, _, _ = session_infrastructure_settings(session)
    with (run_dir / "apply.log").open("a") as log:
        process = popen_factory(
            [
                "terraform",
                f"-chdir={terraform_dir}",
                "apply",
                "-input=false",
                session["terraform_plan_path"],
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            env=environment,
        )
        timed_out = False
        try:
            exit_code = process.wait(
                timeout=session.get("placement_timeout_seconds", PLACEMENT_TIMEOUT_SECONDS)
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=90)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            exit_code = 124
        except KeyboardInterrupt:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=90)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            exit_code = 130

    write_json(run_dir / "apply-result.json", {
        "exit_code": exit_code,
        "finished_unix": time.time(),
        "placement_timeout": timed_out,
    })
    if exit_code != 0:
        cleanup_code = cleanup_fn(run_dir)
        write_json(run_dir / "failure-cleanup-result.json", {
            "cleanup_exit_code": cleanup_code,
            "finished_unix": time.time(),
        })
    return exit_code


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--execute", action="store_true", help="permit one admitted paid Terraform apply")
    result.add_argument("--approval-record", type=Path, required=True)
    result.add_argument("--approved-max-usd-pretax", required=True)
    result.add_argument("--purchase-type", choices=("preemptible", "on-demand"), required=True)
    result.add_argument(
        "--profile",
        choices=tuple(PROFILE_POLICIES),
        default=FULL_TOPOLOGY_PROFILE,
        help="resource shape and lifecycle policy (default: full-topology)",
    )
    result.add_argument("--plan", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["guard"]:
        if len(argv) != 2:
            raise SystemExit("Usage: pilot-session.py guard RUN_DIR")
        return guard_run(Path(argv[1]))
    if argv[:1] == ["cleanup"]:
        if len(argv) != 3 or argv[2] != "--execute":
            raise SystemExit("Usage: pilot-session.py cleanup RUN_DIR --execute")
        return cleanup_until_target(Path(argv[1]))
    args = parser().parse_args(argv)
    try:
        run_dir, session = prepare_session(args)
        guard = spawn_guard(run_dir)
        wait_guard_ready(run_dir, guard)
        session["guard_pid"] = guard.pid
        write_json(run_dir / "session.json", session)
        exit_code = apply_plan(run_dir, session)
        print(json.dumps({"run_dir": str(run_dir), "guard_pid": guard.pid, "apply_exit_code": exit_code}))
        return exit_code
    except (SessionError, json.JSONDecodeError, OSError) as error:
        print(f"pilot-session: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
