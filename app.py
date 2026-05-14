#!/usr/bin/env python3
"""Local web app for converting legacy configs to EX3400 configs."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import threading
import time
import webbrowser
import zipfile
from dataclasses import replace
from datetime import datetime

from flask import Flask, render_template, request, send_file

from converter import SUPPORTED_PLATFORMS, TARGET_MODELS, convert_config


app = Flask(__name__)

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


@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    source_config = ""
    platform = "auto"
    stack_members = 1
    hostname_suffix = "-EX3400"
    target_model = "ex3400_24p"
    strip_review_comments = False
    include_load_hint = False
    template_profile = "none"
    port_overrides_text = ""
    output_config = ""
    stats = None
    report = None
    mapping_csv = ""
    matched_view = []

    if request.method == "POST":
        source_config = request.form.get("source_config", "")
        platform = request.form.get("platform", "auto")
        stack_members = int(request.form.get("stack_members") or 1)
        hostname_suffix = request.form.get("hostname_suffix", "-EX3400")
        target_model = request.form.get("target_model", "ex3400_24p")
        strip_review_comments = request.form.get("strip_review_comments") == "on"
        include_load_hint = request.form.get("include_load_hint") == "on"
        template_profile = request.form.get("template_profile", "none")
        port_overrides_text = request.form.get("port_overrides", "")
        result = convert_config(source_config, platform, stack_members, hostname_suffix, target_model)
        result = apply_port_overrides(result, parse_port_overrides(port_overrides_text))
        profiled_config = apply_template_profile(result.config, template_profile)
        output_config = prepare_output(profiled_config, strip_review_comments, include_load_hint)
        stats = build_stats(output_config, result.warnings, result.source_to_target_ports)
        report = build_report(output_config, result.warnings, result.source_to_target_ports, result.platform)
        mapping_csv = build_mapping_csv(result.source_to_target_ports)
        matched_view = build_matched_view(source_config, output_config)

    return render_template(
        "index.html",
        platforms=SUPPORTED_PLATFORMS,
        target_models=TARGET_MODELS,
        template_profiles=TEMPLATE_PROFILES,
        result=result,
        output_config=output_config,
        source_config=source_config,
        selected_platform=platform,
        stack_members=stack_members,
        hostname_suffix=hostname_suffix,
        selected_target_model=target_model,
        strip_review_comments=strip_review_comments,
        include_load_hint=include_load_hint,
        selected_template_profile=template_profile,
        port_overrides=port_overrides_text,
        stats=stats,
        report=report,
        mapping_csv=mapping_csv,
        matched_view=matched_view,
    )


@app.route("/health")
def health():
    return {"status": "ok"}


@app.route("/batch", methods=["POST"])
def batch_convert():
    files = [file for file in request.files.getlist("config_files") if file and file.filename]
    if not files:
        return "No files uploaded", 400

    platform = request.form.get("platform", "auto")
    target_model = request.form.get("target_model", "ex3400_24p")
    stack_members = int(request.form.get("stack_members") or 1)
    hostname_suffix = request.form.get("hostname_suffix", "-EX3400")
    strip_review_comments = request.form.get("strip_review_comments") == "on"
    include_load_hint = request.form.get("include_load_hint") == "on"
    template_profile = request.form.get("template_profile", "none")

    zip_buffer = io.BytesIO()
    batch_index = []
    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for upload in files:
            raw = upload.read().decode("utf-8", errors="replace")
            result = convert_config(raw, platform, stack_members, hostname_suffix, target_model)
            profiled_config = apply_template_profile(result.config, template_profile)
            output = prepare_output(profiled_config, strip_review_comments, include_load_hint)
            report = build_report(output, result.warnings, result.source_to_target_ports, result.platform)
            base = safe_stem(upload.filename)
            archive.writestr(f"{base}/converted_ex3400.conf", output)
            archive.writestr(f"{base}/port_mapping.csv", build_mapping_csv(result.source_to_target_ports))
            archive.writestr(f"{base}/migration_report.json", json.dumps(report, indent=2))
            archive.writestr(f"{base}/source_config.txt", raw)
            batch_index.append(
                {
                    "file": upload.filename,
                    "detected_platform": result.platform,
                    "set_lines": report["counts"]["set_lines"],
                    "warnings": len(result.warnings),
                    "ports": len(result.source_to_target_ports),
                }
            )
        archive.writestr("batch_index.json", json.dumps(batch_index, indent=2))

    zip_buffer.seek(0)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"ex3400_batch_{stamp}.zip",
    )


def prepare_output(config: str, strip_review_comments: bool, include_load_hint: bool = False) -> str:
    lines = config.splitlines()
    if strip_review_comments:
        lines = [line for line in lines if not line.startswith("# REVIEW")]
    if include_load_hint:
        lines = [
            "# Optional Junos load helper:",
            "# configure",
            "# load set terminal",
            "# paste the set commands below, then end input and run commit check",
            "",
        ] + lines
    return "\n".join(lines).rstrip() + "\n"


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


def apply_port_overrides(result, overrides: dict[str, str]):
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


def build_report(config: str, warnings: list[str], port_map: dict[str, str], platform: str) -> dict:
    lines = [line for line in config.splitlines() if line.strip()]
    categories = categorize_review_items(lines, warnings)
    validations = validate_output(lines, port_map)
    return {
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


def safe_stem(filename: str) -> str:
    stem = os.path.splitext(os.path.basename(filename))[0] or "config"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", stem)[:80]


def open_browser_later(url: str) -> None:
    time.sleep(1.5)
    webbrowser.open(url)


if __name__ == "__main__":
    host = "127.0.0.1"
    port = int(os.environ.get("CONFIG_CONVERT_PORT", "5050"))
    url = f"http://{host}:{port}/?theme=dark"
    if os.environ.get("CONFIG_CONVERT_OPEN_BROWSER") == "1":
        threading.Thread(target=open_browser_later, args=(url,), daemon=True).start()
    app.run(host=host, port=port, debug=False, use_reloader=False)
