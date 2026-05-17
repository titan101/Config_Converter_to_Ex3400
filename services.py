"""Service helpers for the EX3400 converter web app.

This module keeps Flask routes thin and makes export/report/security behavior
testable without running the web server.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import zipfile
from dataclasses import replace
from datetime import datetime
from typing import Any

from converter import ConversionResult, convert_config


APP_VERSION = "0.3.0"

TEMPLATE_PROFILES = {
    "none": {"label": "No standard profile", "lines": []},
    "basic_ops": {
        "label": "Basic ops baseline",
        "lines": [
            "set system services ssh",
            "set protocols lldp interface all",
            "set protocols rstp interface all",
            "set system syslog file messages any notice",
            "set system syslog file interactive-commands interactive-commands any",
        ],
    },
    "field_turnup": {
        "label": "Field turn-up baseline",
        "lines": [
            "set system services ssh",
            "set protocols lldp interface all",
            "set protocols lldp-med interface all",
            "set protocols rstp interface all",
            "set system syslog file messages any any",
            "set system ntp boot-server <ntp-server>",
            "set snmp location \"<site-location>\"",
            "set snmp contact \"<noc-contact>\"",
        ],
    },
}


def process_conversion(
    source_config: str,
    platform: str,
    stack_members: int,
    hostname_suffix: str,
    target_model: str,
    template_profile: str = "none",
    port_overrides_text: str = "",
    strip_review_comments: bool = False,
    include_load_hint: bool = False,
    redact_secrets: bool = True,
) -> dict[str, Any]:
    result = convert_config(source_config, platform, stack_members, hostname_suffix, target_model)
    result = add_input_format_warnings(result, source_config)
    result = apply_port_overrides(result, parse_port_overrides(port_overrides_text))
    profiled_config = apply_template_profile(result.config, template_profile)
    output_config = prepare_output(profiled_config, strip_review_comments, include_load_hint, redact_secrets)
    report = build_report(output_config, result.warnings, result.source_to_target_ports, result.platform)
    return {
        "result": result,
        "output_config": output_config,
        "stats": build_stats(output_config, result.warnings, result.source_to_target_ports),
        "report": report,
        "mapping_csv": build_mapping_csv(result.source_to_target_ports),
        "matched_view": build_matched_view(redact_sensitive_values(source_config) if redact_secrets else source_config, output_config),
    }


def add_input_format_warnings(result: ConversionResult, source_config: str) -> ConversionResult:
    warning = detect_input_format_warning(source_config)
    if not warning or warning in result.warnings:
        return result
    return replace(result, warnings=[warning] + list(result.warnings))


def detect_input_format_warning(source_config: str) -> str | None:
    stripped_lines = [line.strip().lstrip("\ufeff") for line in source_config.splitlines() if line.strip()]
    if not stripped_lines:
        return None
    has_set_lines = any(line.startswith("set ") for line in stripped_lines[:40])
    has_junos_hierarchy = any(line.endswith("{") for line in stripped_lines[:80]) and any(
        line in {"system {", "interfaces {", "routing-options {", "protocols {"} for line in stripped_lines[:80]
    )
    if has_junos_hierarchy and not has_set_lines:
        return (
            "Input appears to be hierarchical Junos. Please paste Junos set commands when possible "
            "(for example, use `show configuration | display set`) to make the conversion more accurate."
        )
    return None


def prepare_output(
    config: str,
    strip_review_comments: bool,
    include_load_hint: bool = False,
    redact_secrets: bool = True,
) -> str:
    lines = config.splitlines()
    if strip_review_comments:
        lines = [line for line in lines if not line.startswith("# REVIEW")]
    output = "\n".join(lines).rstrip() + "\n"
    if redact_secrets:
        output = redact_sensitive_values(output)
    if include_load_hint:
        output = "\n".join(
            [
                "# Optional Junos load helper:",
                "# configure",
                "# load set terminal",
                "# paste the set commands below, then end input and run commit check",
                "",
                output.rstrip(),
            ]
        ).rstrip() + "\n"
    return output


def redact_sensitive_values(config: str) -> str:
    replacements = [
        (r'(authentication-key\s+)"[^"]*"', r'\1"<redacted>"'),
        (r"(authentication-key\s+)\S+", r'\1"<redacted>"'),
        (r"(encrypted-password\s+)\S+", r"\1<redacted>"),
        (r"(plain-text-password\s+)\S+", r"\1<redacted>"),
        (r"(password\s+)\S+", r"\1<redacted>"),
        (r"(secret\s+)\S+", r"\1<redacted>"),
        (r"(key-string\s+)\S+", r"\1<redacted>"),
        (r"(tacacs-server\s+key\s+)\S+", r"\1<redacted>"),
        (r"(radius-server\s+key\s+)\S+", r"\1<redacted>"),
        (r"(snmp-server\s+community\s+)\S+", r"\1<redacted-community>"),
        (r"set snmp community \S+ authorization", "set snmp community <redacted-community> authorization"),
    ]
    redacted = config
    for pattern, replacement in replacements:
        redacted = re.sub(pattern, replacement, redacted, flags=re.I)
    return redacted


def build_stats(config: str, warnings: list[str], port_map: dict[str, str]) -> dict[str, int]:
    lines = [line for line in config.splitlines() if line.strip()]
    return {
        "set_lines": sum(1 for line in lines if line.startswith("set ")),
        "review_lines": sum(1 for line in lines if line.startswith("# REVIEW")),
        "warnings": len(warnings),
        "ports": len(port_map),
    }


def parse_port_overrides(raw: str) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for line in raw.splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned.startswith("#"):
            continue
        if "," in cleaned:
            source, target = cleaned.split(",", 1)
        elif "=" in cleaned:
            source, target = cleaned.split("=", 1)
        elif "->" in cleaned:
            source, target = cleaned.split("->", 1)
        else:
            continue
        source = source.strip()
        target = target.strip()
        if source and target:
            overrides[source] = target
    return overrides


def apply_port_overrides(result: ConversionResult, overrides: dict[str, str]) -> ConversionResult:
    if not overrides:
        return result
    config = result.config
    mapping = dict(result.source_to_target_ports)
    warnings = list(result.warnings)
    for source, new_target in overrides.items():
        old_target = mapping.get(source)
        if not old_target:
            warnings.append(f"Port override ignored for {source}: source port was not present in the generated mapping.")
            continue
        config = replace_interface_token(config, old_target, new_target)
        mapping[source] = new_target
    return replace(result, config=config, warnings=warnings, source_to_target_ports=mapping)


def replace_interface_token(config: str, old: str, new: str) -> str:
    pattern = re.compile(rf"(?<![A-Za-z0-9_./-]){re.escape(old)}(?![A-Za-z0-9_./-])")
    return pattern.sub(new, config)


def apply_template_profile(config: str, profile_name: str) -> str:
    profile = TEMPLATE_PROFILES.get(profile_name, TEMPLATE_PROFILES["none"])
    profile_lines = list(profile["lines"])
    if not profile_lines:
        return config
    lines = config.splitlines()
    merged: list[str] = []
    for line in profile_lines + lines:
        if line and line not in merged:
            merged.append(line)
    return "\n".join(merged) + "\n"


def build_report(config: str, warnings: list[str], port_map: dict[str, str], platform: str) -> dict[str, Any]:
    lines = [line for line in config.splitlines() if line.strip()]
    validations = validate_output(lines, port_map)
    categories = categorize_review_items(lines, warnings)
    return {
        "app_version": APP_VERSION,
        "platform": platform,
        "counts": {
            "set_lines": sum(1 for line in lines if line.startswith("set ")),
            "review_lines": sum(1 for line in lines if line.startswith("# REVIEW")),
            "comment_lines": sum(1 for line in lines if line.startswith("#")),
            "ports": len(port_map),
            "vlans": count_matching(lines, r"^set vlans \S+ vlan-id "),
            "irbs": count_matching(lines, r"^set interfaces irb unit "),
            "static_routes": count_matching(lines, r"^set routing-options static route "),
            "ospf_interfaces": count_matching(lines, r"^set protocols ospf area .* interface "),
            "validation_errors": sum(1 for item in validations if item["severity"] == "error"),
            "validation_warnings": sum(1 for item in validations if item["severity"] == "warning"),
        },
        "categories": categories,
        "validations": validations,
        "port_mapping": port_map,
    }


def count_matching(lines: list[str], pattern: str) -> int:
    regex = re.compile(pattern)
    return sum(1 for line in lines if regex.search(line))


def categorize_review_items(lines: list[str], warnings: list[str]) -> dict[str, list[str]]:
    buckets = {
        "chassis_hardware": [],
        "routing_protocols": [],
        "qos_cos": [],
        "security_services": [],
        "ring_protection": [],
        "management": [],
        "interfaces": [],
        "other": [],
    }
    review_items = [line[2:] if line.startswith("# ") else line for line in lines if line.startswith("# REVIEW")]
    review_items.extend(warnings)
    for item in review_items:
        lower = item.lower()
        if any(word in lower for word in ["chassis", "module", "supervisor", "pic", "mic", "hardware", "optics"]):
            buckets["chassis_hardware"].append(item)
        elif any(word in lower for word in ["ospf", "bgp", "mpls", "routing", "route", "subscriber"]):
            buckets["routing_protocols"].append(item)
        elif any(word in lower for word in ["qos", "cos", "service-policy", "policer", "class-of-service"]):
            buckets["qos_cos"].append(item)
        elif any(word in lower for word in ["firewall", "nat", "vpn", "security", "anti-spoofing", "blade"]):
            buckets["security_services"].append(item)
        elif any(word in lower for word in ["ring", "erps", "raps", "r-aps", "rpl"]):
            buckets["ring_protection"].append(item)
        elif any(word in lower for word in ["snmp", "syslog", "ntp", "aaa", "login", "management"]):
            buckets["management"].append(item)
        elif any(word in lower for word in ["interface", "port", "lag", "lacp", "speed", "duplex"]):
            buckets["interfaces"].append(item)
        else:
            buckets["other"].append(item)
    return {key: value for key, value in buckets.items() if value}


def validate_output(lines: list[str], port_map: dict[str, str]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    set_lines = [line for line in lines if line.startswith("set ")]
    duplicates = sorted({line for line in set_lines if set_lines.count(line) > 1})
    for line in duplicates[:20]:
        findings.append({"severity": "warning", "message": f"Duplicate set line: {line}"})

    vlan_defs: dict[str, str] = {}
    for line in set_lines:
        m = re.match(r"set vlans (\S+) vlan-id (\d+)", line)
        if m:
            vlan_defs[m.group(1)] = m.group(2)
    vlan_names = set(vlan_defs)
    for line in set_lines:
        m = re.match(r"set interfaces \S+ unit \d+ family ethernet-switching vlan members (\S+)", line)
        if m and m.group(1) not in vlan_names and m.group(1) != "all":
            findings.append({"severity": "error", "message": f"VLAN member {m.group(1)} has no matching VLAN definition."})

    irb_units = set()
    bound_irbs = set()
    for line in set_lines:
        m = re.match(r"set interfaces irb unit (\d+) family inet address", line)
        if m:
            irb_units.add(m.group(1))
        m = re.match(r"set vlans \S+ l3-interface irb\.(\d+)", line)
        if m:
            bound_irbs.add(m.group(1))
    for unit in sorted(irb_units - bound_irbs, key=int):
        findings.append({"severity": "warning", "message": f"irb.{unit} has an address but no VLAN l3-interface binding."})

    target_counts: dict[str, int] = {}
    for target in port_map.values():
        target_counts[target] = target_counts.get(target, 0) + 1
    for target, count in target_counts.items():
        if count > 1:
            findings.append({"severity": "error", "message": f"Target port {target} is mapped from {count} source ports."})

    trunk_interfaces = set()
    trunk_members = set()
    for line in set_lines:
        m = re.match(r"set interfaces (\S+) unit \d+ family ethernet-switching interface-mode trunk", line)
        if m:
            trunk_interfaces.add(m.group(1))
        m = re.match(r"set interfaces (\S+) unit \d+ family ethernet-switching vlan members", line)
        if m:
            trunk_members.add(m.group(1))
    for iface in sorted(trunk_interfaces - trunk_members):
        findings.append({"severity": "warning", "message": f"Trunk interface {iface} has no VLAN member lines."})

    if not findings:
        findings.append({"severity": "ok", "message": "No basic validation issues found."})
    return findings


def build_mapping_csv(port_map: dict[str, str]) -> str:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["source_port", "target_port"])
    for source, target in port_map.items():
        writer.writerow([source, target])
    return output.getvalue()


def build_matched_view(source_config: str, output_config: str) -> list[dict[str, str]]:
    source_lines = [line.strip() for line in source_config.splitlines() if line.strip()]
    output_lines = [line for line in output_config.splitlines() if line.startswith("set ")]
    pairs: list[dict[str, str]] = []
    for out in output_lines[:120]:
        token = pick_match_token(out)
        source = next((line for line in source_lines if token and token.lower() in line.lower()), "")
        pairs.append({"source": source or "(generated/default)", "output": out})
    return pairs


def pick_match_token(line: str) -> str:
    for pattern in [
        r"set interfaces (\S+)",
        r"set vlans (\S+)",
        r"set routing-options static route (\S+)",
        r"set protocols ospf area \S+ interface (\S+)",
        r"set snmp community (\S+)",
        r"set system host-name (\S+)",
    ]:
        match = re.search(pattern, line)
        if match:
            return match.group(1).split(".")[0]
    return ""


def build_project_zip(
    source_config: str,
    output_config: str,
    mapping_csv: str,
    report: dict[str, Any],
    include_source: bool,
    base_name: str = "conversion_project",
) -> io.BytesIO:
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("converted_ex3400.conf", output_config)
        archive.writestr("port_mapping.csv", mapping_csv)
        archive.writestr("migration_report.json", json.dumps(report, indent=2))
        if include_source:
            archive.writestr("source_config.txt", source_config)
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "project": safe_stem(base_name),
                    "app_version": APP_VERSION,
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                    "source_included": include_source,
                },
                indent=2,
            ),
        )
    zip_buffer.seek(0)
    return zip_buffer


def build_batch_zip(
    uploads,
    platform: str,
    target_model: str,
    stack_members: int,
    hostname_suffix: str,
    strip_review_comments: bool,
    include_load_hint: bool,
    template_profile: str,
    redact_secrets: bool,
    include_source: bool,
) -> io.BytesIO:
    zip_buffer = io.BytesIO()
    batch_index = []
    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for upload in uploads:
            raw = upload.read().decode("utf-8", errors="replace")
            packaged = process_conversion(
                raw,
                platform,
                stack_members,
                hostname_suffix,
                target_model,
                template_profile,
                "",
                strip_review_comments,
                include_load_hint,
                redact_secrets,
            )
            result = packaged["result"]
            base = safe_stem(upload.filename)
            archive.writestr(f"{base}/converted_ex3400.conf", packaged["output_config"])
            archive.writestr(f"{base}/port_mapping.csv", packaged["mapping_csv"])
            archive.writestr(f"{base}/migration_report.json", json.dumps(packaged["report"], indent=2))
            if include_source:
                archive.writestr(f"{base}/source_config.txt", raw)
            batch_index.append(
                {
                    "file": upload.filename,
                    "detected_platform": result.platform,
                    "set_lines": packaged["report"]["counts"]["set_lines"],
                    "validation_errors": packaged["report"]["counts"]["validation_errors"],
                    "warnings": len(result.warnings),
                    "ports": len(result.source_to_target_ports),
                }
            )
        archive.writestr("batch_index.json", json.dumps(batch_index, indent=2))
    zip_buffer.seek(0)
    return zip_buffer


def safe_stem(filename: str) -> str:
    stem = os.path.splitext(os.path.basename(filename))[0] or "config"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", stem)[:80]
