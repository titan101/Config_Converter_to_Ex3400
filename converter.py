#!/usr/bin/env python3
"""Configuration converter for legacy network devices to Juniper EX3400-24P.

The converter is intentionally rule-based and review-friendly. It creates Junos
`set` commands for common L2/L3 migration items and reports warnings for syntax
that needs an engineer's final decision.
"""

from __future__ import annotations

import argparse
import ipaddress
import re
from dataclasses import dataclass, field
from typing import Iterable


SUPPORTED_PLATFORMS = {
    "auto": "Auto detect",
    "cisco_ios": "Cisco IOS switch / Catalyst",
    "cisco_6500": "Cisco Catalyst 6500",
    "checkpoint": "Check Point Gaia",
    "brocade": "Brocade / Ruckus ICX",
    "juniper_ex3200": "Juniper EX3200 legacy Junos",
    "juniper_ex3300": "Juniper EX3300 legacy Junos",
    "juniper_ex4200": "Juniper EX4200 legacy Junos",
    "juniper_ex4500": "Juniper EX4500 legacy Junos",
    "juniper_m7i": "Juniper M7i Junos",
    "juniper_mx104": "Juniper MX104 Junos",
    "juniper_mx204": "Juniper MX204 Junos",
    "juniper_mx304": "Juniper MX304 Junos",
    "ciena": "Ciena 3920/3930 SAOS switch",
}

TARGET_MODELS = {
    "ex3400_24p": {"label": "EX3400-24P", "ports_per_member": 24},
    "ex3400_48": {"label": "EX3400-48 port", "ports_per_member": 48},
    "ex3400_24p_stack": {"label": "EX3400-24P stack", "ports_per_member": 24},
}

JUNOS_PLATFORMS = {
    "juniper_ex3200",
    "juniper_ex3300",
    "juniper_ex4200",
    "juniper_ex4500",
    "juniper_m7i",
    "juniper_mx104",
    "juniper_mx204",
    "juniper_mx304",
}

MX_PLATFORMS = {"juniper_mx104", "juniper_mx204", "juniper_mx304"}


@dataclass
class PortConfig:
    source_name: str
    target_name: str
    description: str | None = None
    disabled: bool = False
    mode: str | None = None
    access_vlan: str | None = None
    trunk_vlans: list[str] = field(default_factory=list)
    native_vlan: str | None = None
    ip_addresses: list[str] = field(default_factory=list)
    mtu: str | None = None
    lacp_group: str | None = None
    extra_comments: list[str] = field(default_factory=list)


@dataclass
class ConversionResult:
    platform: str
    config: str
    warnings: list[str]
    notes: list[str]
    source_to_target_ports: dict[str, str]


@dataclass
class ConfigModel:
    hostname: str | None = None
    router_id: str | None = None
    vlans: dict[str, str] = field(default_factory=dict)
    ports: dict[str, PortConfig] = field(default_factory=dict)
    static_routes: list[tuple[str, str, str | None]] = field(default_factory=list)
    snmp_communities: list[tuple[str, str | None]] = field(default_factory=list)
    snmp_hosts: list[str] = field(default_factory=list)
    snmp_location: str | None = None
    snmp_contact: str | None = None
    ntp_servers: list[str] = field(default_factory=list)
    syslog_hosts: list[str] = field(default_factory=list)
    users: list[tuple[str, str | None]] = field(default_factory=list)
    ospf_interfaces: list[tuple[str, str]] = field(default_factory=list)
    ospf_interface_options: list[tuple[str, str, str, str]] = field(default_factory=list)
    ospf_networks: list[tuple[str, str, str]] = field(default_factory=list)
    bgp_lines: list[str] = field(default_factory=list)
    extra_set_lines: list[str] = field(default_factory=list)
    review_comments: list[str] = field(default_factory=list)
    ring_comments: list[str] = field(default_factory=list)


class PortAllocator:
    """Assign source ports to EX3400-24P member ports in discovery order."""

    def __init__(self, stack_members: int = 1, ports_per_member: int = 24) -> None:
        self.stack_members = max(1, stack_members)
        self.ports_per_member = ports_per_member
        self._map: dict[str, str] = {}
        self._next = 0

    def get(self, source_port: str) -> str:
        source_port = source_port.strip()
        if source_port in self._map:
            return self._map[source_port]
        member = min(self._next // self.ports_per_member, self.stack_members - 1)
        port = self._next % self.ports_per_member
        target = f"ge-{member}/0/{port}"
        self._map[source_port] = target
        self._next += 1
        return target

    def bind(self, source_port: str, target_port: str) -> str:
        self._map[source_port.strip()] = target_port
        return target_port

    @property
    def mapping(self) -> dict[str, str]:
        return dict(self._map)


def convert_config(
    raw_config: str,
    source_platform: str = "auto",
    stack_members: int = 1,
    hostname_suffix: str = "-EX3400",
    target_model: str = "ex3400_24p",
) -> ConversionResult:
    platform = detect_platform(raw_config) if source_platform == "auto" else source_platform
    ports_per_member = TARGET_MODELS.get(target_model, TARGET_MODELS["ex3400_24p"])["ports_per_member"]  # type: ignore[index]
    allocator = PortAllocator(stack_members=stack_members, ports_per_member=int(ports_per_member))
    warnings: list[str] = []

    if platform in {"cisco_ios", "cisco_6500"}:
        model = parse_cisco_ios(raw_config, allocator, warnings, platform)
    elif platform == "checkpoint":
        model = parse_checkpoint(raw_config, allocator, warnings)
    elif platform == "brocade":
        model = parse_brocade(raw_config, allocator, warnings)
    elif platform in JUNOS_PLATFORMS:
        model = parse_junos(raw_config, allocator, warnings, platform, target_model)
    elif platform == "ciena":
        model = parse_ciena(raw_config, allocator, warnings)
    else:
        model = ConfigModel()
        warnings.append("Could not identify the source platform. Parsed only generic static routes, SNMP, NTP, and syslog lines.")
        parse_generic_lines(raw_config.splitlines(), model)

    config_lines, notes = render_ex3400(model, warnings, hostname_suffix)
    return ConversionResult(
        platform=platform,
        config="\n".join(config_lines) + "\n",
        warnings=warnings,
        notes=notes,
        source_to_target_ports=allocator.mapping,
    )


def detect_platform(raw_config: str) -> str:
    text = raw_config.lower()
    if re.search(r"^set hostname\b|^set interface (eth|mgmt|lo)\b|^set static-route\b|^set bgp external\b", text, re.M):
        return "checkpoint"
    if re.search(r"^interface ve \d+|^vlan \d+ name|^\s*(tagged|untagged) ethe\b|^ip router-id\b", text, re.M):
        return "brocade"
    if "mx304" in text or "mx-304" in text:
        return "juniper_mx304"
    if "mx204" in text or "mx-204" in text:
        return "juniper_mx204"
    if "mx104" in text or "mx-104" in text:
        return "juniper_mx104"
    if "set system" in text or "set interfaces" in text:
        if "set interfaces vlan unit" in text or re.search(r"interface vlan\.\d+", text):
            return "juniper_ex3200"
        if re.search(r"\bset interfaces (so-|t1-|t3-|at-|fe-|ge-|xe-)", text) and "routing-options" in text:
            return "juniper_m7i"
        return "juniper_ex3300"
    if re.search(r"^\s*system\s*\{", text, re.M) and re.search(r"^\s*interfaces\s*\{", text, re.M):
        if "mx304" in text:
            return "juniper_mx304"
        if "mx204" in text:
            return "juniper_mx204"
        if "mx104" in text:
            return "juniper_mx104"
        if "routing-options" in text or "protocols" in text:
            return "juniper_mx204"
        return "juniper_ex3300"
    if "sup-bootflash" in text or "module provision" in text or "cat6500" in text:
        return "cisco_6500"
    if re.search(r"^interface\s+(gigabitethernet|fastethernet|tengigabitethernet|vlan)", text, re.M):
        return "cisco_ios"
    if (
        "ciena" in text
        or re.search(r"\bport set\b|\bvlan create\b|\bflow service\b", text)
        or re.search(r"\b(3920|3930|saos|ring-protection|erps|r-aps|raps|logical-ring|virtual-ring)\b", text)
    ):
        return "ciena"
    return "unknown"


def normalize_vlan_name(name: str | None, vlan_id: str) -> str:
    if not name:
        return f"VLAN_{vlan_id}"
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    return cleaned[:60] or f"VLAN_{vlan_id}"


def expand_vlan_list(value: str) -> list[str]:
    vlans: list[str] = []
    value = value.replace("add", "").replace("except", "")
    for part in re.split(r"[, ]+", value.strip()):
        if not part:
            continue
        if part.lower() in {"all", "none"}:
            return [part.lower()]
        if "-" in part:
            start, end = part.split("-", 1)
            if start.isdigit() and end.isdigit():
                vlans.extend(str(v) for v in range(int(start), int(end) + 1))
        elif part.isdigit():
            vlans.append(part)
    return dedupe(vlans)


def dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def parse_cisco_ios(raw_config: str, allocator: PortAllocator, warnings: list[str], platform: str) -> ConfigModel:
    model = ConfigModel()
    lines = raw_config.splitlines()
    current_iface: PortConfig | None = None
    current_vlan: str | None = None
    in_router: str | None = None

    for original in lines:
        line = original.strip()
        is_top_level = bool(line) and not original[:1].isspace()
        if not line or line.startswith("!"):
            if line == "!":
                current_iface = None
                current_vlan = None
                in_router = None
            continue

        if line.startswith("hostname "):
            model.hostname = line.split(None, 1)[1]
            continue

        vlan_match = re.match(r"vlan\s+(\d+)$", line, re.I)
        if vlan_match:
            current_iface = None
            in_router = None
            current_vlan = vlan_match.group(1)
            model.vlans.setdefault(current_vlan, f"VLAN_{current_vlan}")
            continue

        if current_vlan and line.lower().startswith("name "):
            model.vlans[current_vlan] = normalize_vlan_name(line.split(None, 1)[1], current_vlan)
            continue

        iface_match = re.match(r"interface\s+(.+)$", line, re.I)
        if iface_match:
            source_name = iface_match.group(1).strip()
            current_vlan = None
            in_router = None
            if source_name.lower().startswith("vlan"):
                vlan_id = re.findall(r"\d+", source_name)[0]
                target = f"irb.{vlan_id}"
                current_iface = PortConfig(source_name=source_name, target_name=target)
                model.ports[source_name] = current_iface
                model.vlans.setdefault(vlan_id, f"VLAN_{vlan_id}")
            elif source_name.lower().startswith(("port-channel", "po")):
                group = re.findall(r"\d+", source_name)
                target = f"ae{group[0] if group else len(model.ports)}"
                current_iface = PortConfig(source_name=source_name, target_name=target)
                model.ports[source_name] = current_iface
            else:
                current_iface = PortConfig(source_name=source_name, target_name=allocator.get(source_name))
                model.ports[source_name] = current_iface
            continue

        if re.match(r"router\s+ospf\b", line, re.I):
            in_router = "ospf"
            continue
        if re.match(r"router\s+bgp\b", line, re.I):
            in_router = "bgp"
            model.bgp_lines.append(f"# REVIEW Cisco BGP source: {line}")
            continue

        if current_iface and not is_top_level:
            parse_cisco_interface_line(line, current_iface, model, warnings)
            continue
        if current_iface and is_top_level:
            current_iface = None

        if in_router == "ospf" and not is_top_level:
            network = re.match(r"network\s+(\S+)\s+(\S+)\s+area\s+(\S+)", line, re.I)
            if network:
                model.ospf_networks.append((network.group(1), network.group(2), network.group(3)))
            elif line.startswith("passive-interface"):
                model.review_comments.append(f"REVIEW Cisco OSPF passive-interface: {line}")
            continue
        if in_router == "ospf" and is_top_level:
            in_router = None

        if in_router == "bgp" and not is_top_level:
            model.bgp_lines.append(f"# REVIEW Cisco BGP source: {line}")
            continue
        if in_router == "bgp" and is_top_level:
            in_router = None

        parse_common_cisco_line(line, model)

    if platform == "cisco_6500":
        warnings.append("Catalyst 6500 module, VSS, supervisor, service-module, and hardware QoS commands are chassis-specific and were not translated.")
    return model


def parse_cisco_interface_line(line: str, port: PortConfig, model: ConfigModel, warnings: list[str]) -> None:
    lower = line.lower()
    if lower.startswith("description "):
        port.description = line.split(None, 1)[1].strip()
    elif lower == "shutdown":
        port.disabled = True
    elif lower.startswith("switchport mode "):
        mode = line.split()[-1].lower()
        if mode in {"access", "trunk"}:
            port.mode = mode
    elif lower.startswith("switchport access vlan "):
        vlan = line.split()[-1]
        port.access_vlan = vlan
        port.mode = port.mode or "access"
        model.vlans.setdefault(vlan, f"VLAN_{vlan}")
    elif lower.startswith("switchport trunk native vlan "):
        port.native_vlan = line.split()[-1]
        model.vlans.setdefault(port.native_vlan, f"VLAN_{port.native_vlan}")
    elif lower.startswith("switchport trunk allowed vlan"):
        vlan_text = line.split("vlan", 1)[1]
        port.trunk_vlans = dedupe(port.trunk_vlans + expand_vlan_list(vlan_text))
        port.mode = "trunk"
        for vlan in port.trunk_vlans:
            if vlan.isdigit():
                model.vlans.setdefault(vlan, f"VLAN_{vlan}")
    elif lower.startswith("ip address "):
        parts = line.split()
        if len(parts) >= 4:
            prefix = netmask_to_prefix(parts[3])
            port.ip_addresses.append(f"{parts[2]}/{prefix}")
    elif lower.startswith("mtu "):
        port.mtu = line.split()[-1]
    elif lower.startswith("channel-group "):
        group = re.findall(r"\d+", line)
        if group:
            port.lacp_group = group[0]
            port.extra_comments.append(f"source channel-group {line}")
    elif lower.startswith("spanning-tree portfast"):
        port.extra_comments.append("enable edge STP behavior")
    elif lower.startswith(("speed ", "duplex ", "storm-control ", "service-policy ", "mls qos")):
        port.extra_comments.append(f"REVIEW Cisco interface command: {line}")
    elif lower.startswith("no switchport"):
        port.mode = None
    elif line and not lower.startswith(("no ip", "load-interval", "negotiation auto")):
        if len(port.extra_comments) < 12:
            port.extra_comments.append(f"REVIEW Cisco interface command: {line}")


def parse_common_cisco_line(line: str, model: ConfigModel) -> None:
    lower = line.lower()
    if lower.startswith("ip route "):
        parts = line.split()
        if len(parts) >= 5:
            prefix = netmask_to_prefix(parts[3])
            model.static_routes.append((f"{parts[2]}/{prefix}", parts[4], None))
    elif lower.startswith("snmp-server community "):
        parts = line.split()
        permission = parts[3] if len(parts) >= 4 and parts[3].upper() in {"RO", "RW"} else None
        model.snmp_communities.append((parts[2], permission))
    elif lower.startswith("snmp-server host "):
        parts = line.split()
        if len(parts) >= 3:
            model.snmp_hosts.append(parts[2])
    elif lower.startswith("snmp-server location "):
        model.snmp_location = line.split(None, 2)[2]
    elif lower.startswith("snmp-server contact "):
        model.snmp_contact = line.split(None, 2)[2]
    elif lower.startswith("ntp server "):
        model.ntp_servers.append(line.split()[2])
    elif lower.startswith("logging host "):
        model.syslog_hosts.append(line.split()[2])
    elif lower.startswith("username "):
        parts = line.split()
        user = parts[1]
        privilege = parts[3] if "privilege" in parts else None
        model.users.append((user, privilege))


def parse_checkpoint(raw_config: str, allocator: PortAllocator, warnings: list[str]) -> ConfigModel:
    model = ConfigModel()
    cp_ports: dict[str, PortConfig] = {}

    def checkpoint_target(cpif: str) -> str:
        if cpif == "Mgmt":
            return "me0"
        if cpif == "lo":
            return "lo0"
        base = cpif.split(".", 1)[0]
        if re.match(r"eth\d+$", base):
            return allocator.get(base)
        return allocator.get(base)

    def get_port(cpif: str) -> PortConfig:
        base = cpif.split(".", 1)[0]
        target = checkpoint_target(base)
        port = cp_ports.get(base)
        if not port:
            port = PortConfig(source_name=base, target_name=target)
            cp_ports[base] = port
            model.ports[base] = port
        return port

    for original in raw_config.splitlines():
        line = original.strip()
        lower = line.lower()
        if not line:
            continue
        if lower.startswith("set hostname "):
            model.hostname = line.split(None, 2)[2]
        elif lower.startswith("set interface lo ipv4-address "):
            ip, prefix = checkpoint_ip_and_prefix(line)
            if ip and prefix:
                model.extra_set_lines.append(f"set interfaces lo0 unit 0 family inet address {ip}/{prefix}")
        elif lower.startswith("set interface mgmt "):
            parse_checkpoint_mgmt_line(line, model)
        elif re.match(r"set interface eth\d+(?:\.\d+)? ", line, re.I):
            cpif = line.split()[2]
            if "." in cpif and " ipv4-address " in lower:
                vlan_id = cpif.split(".", 1)[1]
                ip, prefix = checkpoint_ip_and_prefix(line)
                if ip and prefix:
                    model.vlans.setdefault(vlan_id, f"V{vlan_id}")
                    model.extra_set_lines.append(f"set interfaces irb unit {vlan_id} family inet address {ip}/{prefix}")
                    model.extra_set_lines.append(f"set vlans V{vlan_id} l3-interface irb.{vlan_id}")
                continue
            port = get_port(cpif)
            parse_checkpoint_interface_line(line, port, model)
        elif re.match(r"add interface eth\d+ vlan \d+", line, re.I):
            parts = line.split()
            cpif = parts[2]
            vlan_id = parts[4]
            port = get_port(cpif)
            port.mode = "trunk"
            port.trunk_vlans = dedupe(port.trunk_vlans + [vlan_id])
            model.vlans.setdefault(vlan_id, f"V{vlan_id}")
        elif lower.startswith("set static-route "):
            parse_checkpoint_static_route(line, model)
        elif re.match(r"set ospf instance default interface \S+ area backbone on$", line, re.I):
            cpif = line.split()[5]
            target = checkpoint_target(cpif)
            model.ospf_interfaces.append(("0.0.0.0", f"{target}.0"))
        elif lower.startswith("set ospf instance default interface "):
            parse_checkpoint_ospf_option(line, model, checkpoint_target)
        elif lower.startswith("set bgp external remote-as "):
            parse_checkpoint_bgp_line(line, model)
        elif lower.startswith("set route-redistribution to ospf2 instance default from interface "):
            parse_checkpoint_redistribution(line, model, checkpoint_target)
        elif lower.startswith(("set snmp ", "add snmp ", "set ntp ", "set syslog ")):
            model.review_comments.append(f"REVIEW Check Point management command: {line}")

    warnings.append("Check Point Gaia conversion is based on Gaia CLI syntax. NAT, firewall policy, VPN, clustering, anti-spoofing, and security blade configuration do not map to an EX3400 switch and were not translated.")
    return model


def checkpoint_ip_and_prefix(line: str) -> tuple[str | None, str | None]:
    ip_match = re.search(r"ipv4-address\s+([0-9.]+)", line, re.I)
    prefix_match = re.search(r"mask-length\s+(\d+)", line, re.I)
    return (ip_match.group(1) if ip_match else None, prefix_match.group(1) if prefix_match else None)


def parse_checkpoint_mgmt_line(line: str, model: ConfigModel) -> None:
    lower = line.lower()
    if " ipv4-address " in lower:
        ip, prefix = checkpoint_ip_and_prefix(line)
        if ip and prefix:
            model.extra_set_lines.append(f"set interfaces me0 unit 0 family inet address {ip}/{prefix}")
    elif " mtu " in lower:
        model.extra_set_lines.append(f"set interfaces me0 mtu {line.split()[-1]}")
    elif " comments " in lower:
        desc = line.split(" comments ", 1)[1].strip().strip('"')
        model.extra_set_lines.append(f"set interfaces me0 description \"{desc}\"")


def parse_checkpoint_interface_line(line: str, port: PortConfig, model: ConfigModel) -> None:
    lower = line.lower()
    if " comments " in lower:
        port.description = line.split(" comments ", 1)[1].strip().strip('"')
    elif " mtu " in lower:
        port.mtu = line.split()[-1]
    elif re.search(r"\bstate off\b", lower):
        port.disabled = True
    elif " ipv4-address " in lower:
        ip, prefix = checkpoint_ip_and_prefix(line)
        if ip and prefix:
            port.ip_addresses.append(f"{ip}/{prefix}")
    elif " auto-negotiation " in lower or " link-speed " in lower:
        port.extra_comments.append(f"REVIEW Check Point link setting: {line}")


def parse_checkpoint_static_route(line: str, model: ConfigModel) -> None:
    parts = line.split()
    if len(parts) < 3:
        return
    prefix = "0.0.0.0/0" if parts[2] == "default" else parts[2]
    nh_match = re.search(r"nexthop gateway address\s+([0-9.]+)", line, re.I)
    if nh_match:
        model.static_routes.append((prefix, nh_match.group(1), None))


def parse_checkpoint_ospf_option(line: str, model: ConfigModel, mapper) -> None:
    option_map = {
        "hello-interval": "hello-interval",
        "dead-interval": "dead-interval",
        "cost": "metric",
        "priority": "priority",
        "retransmit-interval": "retransmit-interval",
    }
    parts = line.split()
    if len(parts) < 8:
        return
    cpif = parts[5]
    for cp_key, junos_key in option_map.items():
        if cp_key in parts:
            value = parts[parts.index(cp_key) + 1]
            model.ospf_interface_options.append(("0.0.0.0", f"{mapper(cpif)}.0", junos_key, value))


def parse_checkpoint_bgp_line(line: str, model: ConfigModel) -> None:
    m = re.search(r"remote-as\s+(\d+).*peer\s+([0-9.]+)", line, re.I)
    if not m:
        model.bgp_lines.append(f"# REVIEW Check Point BGP source: {line}")
        return
    asn, peer = m.group(1), m.group(2)
    add(model.extra_set_lines, "set protocols bgp group EBGP type external")
    add(model.extra_set_lines, f"set protocols bgp group EBGP neighbor {peer}")
    add(model.extra_set_lines, f"set protocols bgp group EBGP neighbor {peer} peer-as {asn}")
    key = re.search(r"authtype md5 secret\s+(\S+)", line, re.I)
    if key:
        add(model.extra_set_lines, f"set protocols bgp group EBGP neighbor {peer} authentication-key \"{key.group(1)}\"")
    desc = re.search(r"description\s+\"?(.+?)\"?$", line, re.I)
    if desc and " peer " not in desc.group(1):
        add(model.extra_set_lines, f"set protocols bgp group EBGP description \"{desc.group(1).strip()}\"")


def parse_checkpoint_redistribution(line: str, model: ConfigModel, mapper) -> None:
    cpif_match = re.search(r"from interface\s+(\S+)", line, re.I)
    if not cpif_match:
        return
    cpif = cpif_match.group(1)
    metric_match = re.search(r"metric\s+(\d+)", line, re.I)
    policy = "REDIST_" + re.sub(r"[^A-Za-z0-9_]", "_", cpif)
    iface = mapper(cpif) + ".0"
    add(model.extra_set_lines, f"set policy-options policy-statement {policy} term 1 from interface {iface}")
    if metric_match:
        add(model.extra_set_lines, f"set policy-options policy-statement {policy} term 1 then metric {metric_match.group(1)}")
    add(model.extra_set_lines, f"set policy-options policy-statement {policy} term 1 then accept")
    add(model.extra_set_lines, f"set protocols ospf export {policy}")


def netmask_to_prefix(mask: str) -> int:
    try:
        return ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen
    except ValueError:
        return 32


def parse_brocade(raw_config: str, allocator: PortAllocator, warnings: list[str]) -> ConfigModel:
    model = ConfigModel()
    current_vlan: str | None = None
    current_interface_type: str | None = None
    current_interface_id: str | None = None
    ve_to_vlan: dict[str, str] = {}
    highest_port = 0

    for original in raw_config.splitlines():
        line = original.strip()
        lower = line.lower()
        if not line:
            continue
        if line.startswith("!"):
            current_vlan = None
            current_interface_type = None
            current_interface_id = None
            continue
        if lower.startswith("hostname "):
            model.hostname = line.split()[1]
        elif lower.startswith("ip router-id "):
            model.router_id = line.split()[2]
        elif re.match(r"vlan\s+\d+", lower):
            vlan_id = line.split()[1]
            current_vlan = vlan_id
            name_match = re.search(r"\bname\s+\"?([^\"\n]+)\"?", line, re.I)
            model.vlans[vlan_id] = normalize_vlan_name(name_match.group(1).strip() if name_match else None, vlan_id)
        elif current_vlan and lower.startswith("router-interface ve "):
            ve = line.split()[-1]
            ve_to_vlan[ve] = current_vlan
            model.vlans.setdefault(current_vlan, f"VLAN_{current_vlan}")
        elif current_vlan and lower.startswith("tagged ethe"):
            for port_id in parse_brocade_port_members(line):
                highest_port = max(highest_port, int(port_id))
                port = model.ports.setdefault(port_id, PortConfig(source_name=f"ethernet {port_id}", target_name=brocade_target_port(port_id, allocator)))
                port.mode = "trunk"
                port.trunk_vlans = dedupe(port.trunk_vlans + [current_vlan])
        elif current_vlan and lower.startswith("untagged ethe"):
            for port_id in parse_brocade_port_members(line):
                highest_port = max(highest_port, int(port_id))
                port = model.ports.setdefault(port_id, PortConfig(source_name=f"ethernet {port_id}", target_name=brocade_target_port(port_id, allocator)))
                if port.trunk_vlans:
                    port.mode = "trunk"
                    port.native_vlan = current_vlan
                else:
                    port.mode = "access"
                    port.access_vlan = current_vlan
        elif re.match(r"interface ethernet \d+", lower):
            current_interface_type = "ethernet"
            current_interface_id = line.split()[2]
            highest_port = max(highest_port, int(current_interface_id))
            model.ports.setdefault(current_interface_id, PortConfig(source_name=f"ethernet {current_interface_id}", target_name=brocade_target_port(current_interface_id, allocator)))
        elif re.match(r"interface ve \d+", lower):
            current_interface_type = "ve"
            current_interface_id = line.split()[2]
        elif re.match(r"interface loopback \d+", lower):
            current_interface_type = "loopback"
            current_interface_id = line.split()[2]
        elif current_interface_type == "ethernet" and current_interface_id:
            parse_brocade_ethernet_line(line, model.ports[current_interface_id])
        elif current_interface_type == "ve" and current_interface_id:
            parse_brocade_ve_line(line, current_interface_id, ve_to_vlan, model)
        elif current_interface_type == "loopback" and lower.startswith("ip address "):
            parts = line.split()
            if len(parts) >= 3:
                model.extra_set_lines.append(f"set interfaces lo0 unit 0 family inet address {parts[2]}/32")
        elif lower.startswith("ip route "):
            parse_brocade_static_route(line, model)
        elif lower.startswith(("snmp-server ", "logging ", "ntp ")):
            model.review_comments.append(f"REVIEW Brocade management command: {line}")
        elif lower.startswith(("router ospf", "router bgp")):
            model.review_comments.append(f"REVIEW Brocade routing block start: {line}")

    if highest_port >= 25:
        warnings.append("Brocade config references ports 25 or higher. Auto-mapped those to later EX3400 stack members where possible.")
    warnings.append("Brocade conversion imports VLANs, tagged/untagged memberships, VE/IRB interfaces, OSPF interface hints, loopback, static routes, and descriptions. Review Brocade-specific STP, stacking, ACL, QoS, and routing policy commands.")
    return model


def brocade_target_port(port_id: str, allocator: PortAllocator) -> str:
    p = int(port_id)
    source = f"ethernet {port_id}"
    if 1 <= p <= 24:
        return allocator.bind(source, f"ge-0/0/{p - 1}")
    if 25 <= p <= 48:
        return allocator.bind(source, f"ge-1/0/{p - 25}")
    if p == 49:
        return allocator.bind(source, "xe-0/1/0")
    if p == 50:
        return allocator.bind(source, "xe-1/1/0")
    return allocator.get(source)


def parse_brocade_port_members(line: str) -> list[str]:
    text = line.replace(",", " ")
    tokens = text.split()
    nums: list[int] = []
    i = 0
    last: int | None = None
    while i < len(tokens):
        token = tokens[i]
        if token.isdigit():
            value = int(token)
            if i >= 2 and tokens[i - 1].lower() == "to" and last is not None:
                nums.extend(range(last + 1, value + 1))
            else:
                nums.append(value)
            last = value
        i += 1
    return [str(n) for n in nums]


def parse_brocade_ethernet_line(line: str, port: PortConfig) -> None:
    lower = line.lower()
    if lower.startswith("port-name "):
        port.description = line.split(None, 1)[1].strip().strip('"').replace(" ", "_")
    elif lower.startswith("disable"):
        port.disabled = True
    elif lower.startswith("mtu "):
        port.mtu = line.split()[1]
    elif lower.startswith(("speed-duplex ", "dual-mode ", "stp-", "spanning-tree ")):
        port.extra_comments.append(f"REVIEW Brocade interface command: {line}")


def parse_brocade_ve_line(line: str, ve: str, ve_to_vlan: dict[str, str], model: ConfigModel) -> None:
    lower = line.lower()
    vlan_id = ve_to_vlan.get(ve, ve)
    if lower.startswith("ip address "):
        parts = line.split()
        if len(parts) >= 4:
            prefix = netmask_to_prefix(parts[3])
            model.vlans.setdefault(vlan_id, f"VLAN_{vlan_id}")
            model.extra_set_lines.append(f"set vlans {model.vlans[vlan_id]} l3-interface irb.{vlan_id}")
            model.extra_set_lines.append(f"set interfaces irb unit {vlan_id} family inet address {parts[2]}/{prefix}")
    elif lower.startswith("ip ospf area "):
        area = line.split()[3]
        model.ospf_interfaces.append((area, f"irb.{vlan_id}"))
    elif lower.startswith("ip ospf cost "):
        cost = line.split()[3]
        model.ospf_interface_options.append(("__lookup__", f"irb.{vlan_id}", "metric", cost))


def parse_brocade_static_route(line: str, model: ConfigModel) -> None:
    parts = line.split()
    if len(parts) < 5:
        return
    prefix = f"{parts[2]}/{netmask_to_prefix(parts[3])}"
    next_hop = "discard" if any(part.lower() == "null0" for part in parts[4:]) else parts[4]
    model.static_routes.append((prefix, next_hop, None))


def parse_junos(
    raw_config: str,
    allocator: PortAllocator,
    warnings: list[str],
    platform: str,
    target_model: str = "ex3400_24p",
) -> ConfigModel:
    model = ConfigModel()
    lines = to_set_lines(raw_config)
    legacy_vlan_l3: dict[str, str] = {}

    for line in lines:
        stripped = line.strip().rstrip(";")
        if not stripped.startswith("set "):
            continue
        parts = stripped.split()
        if len(parts) < 3:
            continue

        if stripped.startswith("set system host-name "):
            model.hostname = stripped.split()[-1]
        elif stripped.startswith("set vlans "):
            parse_junos_vlan_line(stripped, model, legacy_vlan_l3)
        elif stripped.startswith("set interfaces "):
            parse_junos_interface_line(stripped, model, allocator, warnings, platform, target_model)
        elif stripped.startswith((
            "set routing-options static route ",
            "set routing-options router-id ",
            "set routing-options autonomous-system ",
        )):
            model.extra_set_lines.append(stripped)
        elif stripped.startswith("set snmp "):
            model.extra_set_lines.append(stripped)
        elif stripped.startswith((
            "set system services ",
            "set system name-server ",
            "set system ntp ",
            "set system syslog ",
            "set system login user ",
            "set system time-zone ",
        )):
            model.extra_set_lines.append(stripped)
        elif stripped.startswith(("set protocols ospf ", "set protocols bgp ", "set policy-options ")):
            model.extra_set_lines.append(stripped)
        elif stripped.startswith((
            "set chassis ",
            "set forwarding-options ",
            "set class-of-service ",
            "set firewall ",
            "set protocols mpls ",
            "set protocols isis ",
        )):
            model.review_comments.append(f"REVIEW source Junos platform-specific command: {stripped}")

    for vlan_name, l3_interface in legacy_vlan_l3.items():
        vlan_id = find_vlan_id_by_name(model, vlan_name)
        if vlan_id and l3_interface.startswith("vlan."):
            unit = l3_interface.split(".", 1)[1]
            model.extra_set_lines.append(f"set vlans {model.vlans[vlan_id]} l3-interface irb.{unit}")
            warnings.append(f"Mapped legacy RVI {l3_interface} to EX3400 ELS interface irb.{unit}.")

    if platform == "juniper_m7i":
        warnings.append("M7i routing/MPLS, PIC, SONET, ATM, firewall filter, and service-provider features may not exist on an EX3400 and were preserved only when broadly compatible.")
    if platform in MX_PLATFORMS:
        mx_model = SUPPORTED_PLATFORMS.get(platform, "Juniper MX Junos")
        warnings.append(f"{mx_model} routing/MPLS, subscriber, services, chassis, MIC/PIC, firewall filter, and service-provider features may not exist on an EX3400 and were preserved only when broadly compatible.")
    if platform == "juniper_ex4500":
        warnings.append("EX4500 physical xe-* ports were auto-assigned to EX3400 target ports. Verify optics, speed, and uplink/downlink roles.")
    return model


def to_set_lines(raw_config: str) -> list[str]:
    if re.search(r"^\s*set\s+", raw_config, re.M):
        return [line.strip().lstrip("\ufeff") for line in raw_config.splitlines() if line.strip()]
    return flatten_junos_hierarchy(raw_config)


def flatten_junos_hierarchy(raw_config: str) -> list[str]:
    lines: list[str] = []
    stack: list[list[str]] = []
    for original in raw_config.splitlines():
        line = original.strip().lstrip("\ufeff")
        if not line or line.startswith(("#", "/*", "*")):
            continue
        line = line.split("/*", 1)[0].strip()
        if line.endswith("{"):
            token = line[:-1].strip()
            if token:
                stack.append(token.split())
            continue
        if line == "}" or line == "};":
            if stack:
                stack.pop()
            continue
        if line.endswith(";"):
            command = line[:-1].strip()
            if command:
                prefix = [part for group in stack for part in group]
                lines.append("set " + " ".join(prefix + command.split()))
    return lines


def parse_junos_vlan_line(line: str, model: ConfigModel, legacy_vlan_l3: dict[str, str]) -> None:
    parts = line.split()
    if len(parts) < 4:
        return
    vlan_name = parts[2]
    if "vlan-id" in parts:
        vlan_id = parts[parts.index("vlan-id") + 1]
        model.vlans[vlan_id] = normalize_vlan_name(vlan_name, vlan_id)
    elif "l3-interface" in parts:
        legacy_vlan_l3[vlan_name] = parts[parts.index("l3-interface") + 1]


def find_vlan_id_by_name(model: ConfigModel, vlan_name: str) -> str | None:
    safe_name = normalize_vlan_name(vlan_name, "0")
    for vlan_id, name in model.vlans.items():
        if name == safe_name or name == vlan_name:
            return vlan_id
    return None


def parse_junos_interface_line(
    line: str,
    model: ConfigModel,
    allocator: PortAllocator,
    warnings: list[str],
    platform: str,
    target_model: str = "ex3400_24p",
) -> None:
    parts = line.split()
    source = parts[2]
    is_legacy_rvi = source == "vlan"
    target = source
    if is_physical_interface(source):
        target = map_junos_physical_interface(source, allocator, platform, target_model)
    elif is_legacy_rvi:
        target = "irb"
    port = model.ports.setdefault(source, PortConfig(source_name=source, target_name=target))

    translated = line.replace(f"set interfaces {source}", f"set interfaces {target}", 1)
    translated = translated.replace(" family ethernet-switching port-mode ", " family ethernet-switching interface-mode ")
    translated = translated.replace("set interfaces irb unit", "set interfaces irb unit")

    if " description " in line:
        port.description = line.split(" description ", 1)[1].strip('"')
    elif " disable" in line.split(" unit ")[0]:
        port.disabled = True
    elif " family inet address " in line:
        model.extra_set_lines.append(translated)
    elif " family ethernet-switching" in line:
        model.extra_set_lines.append(translated)
    elif " native-vlan-id " in line:
        model.extra_set_lines.append(translated)
    elif " mtu " in line:
        port.mtu = parts[-1]
        model.extra_set_lines.append(translated)
    elif source.startswith(("vlan", "irb")):
        model.extra_set_lines.append(translated.replace("set interfaces vlan unit", "set interfaces irb unit"))
    else:
        model.review_comments.append(f"REVIEW source Junos interface command: {line}")
        if platform in {"juniper_m7i", "juniper_ex4500", *MX_PLATFORMS} and source != target:
            warning = f"Mapped {source} to {target}; verify optics, speed, and port role."
            if warning not in warnings:
                warnings.append(warning)


def is_physical_interface(name: str) -> bool:
    return bool(re.match(r"^(ge|xe|fe|et)-\d+/\d+/\d+$|^(ge|xe|fe|et)-\d+/\d+/\d+:\d+$", name))


def map_junos_physical_interface(source: str, allocator: PortAllocator, platform: str, target_model: str) -> str:
    if platform in {"juniper_m7i", "juniper_ex4500", *MX_PLATFORMS}:
        return allocator.get(source)
    if platform == "juniper_ex4200":
        match = re.match(r"^(ge|xe|fe)-(\d+)/(\d+)/(\d+)$", source)
        if not match:
            return source
        speed, member, _pic, port = match.groups()
        member_i = int(member)
        port_i = int(port)
        ports_per_member = int(TARGET_MODELS.get(target_model, TARGET_MODELS["ex3400_24p"])["ports_per_member"])  # type: ignore[index]
        if ports_per_member == 48:
            target = f"{speed}-{member_i}/0/{port_i}"
        else:
            absolute = member_i * 48 + port_i
            target = f"{speed}-{absolute // 24}/0/{absolute % 24}"
        return allocator.bind(source, target)
    return source


def parse_ciena(raw_config: str, allocator: PortAllocator, warnings: list[str]) -> ConfigModel:
    model = ConfigModel()
    current_port: PortConfig | None = None
    for original in raw_config.splitlines():
        line = original.strip()
        lower = line.lower()
        if not line or line.startswith(("!", "#")):
            current_port = None
            continue
        if lower.startswith(("hostname ", "system name ", "system set name ")):
            model.hostname = line.split()[-1].strip('"')
        elif is_ciena_ring_line(lower):
            mapped_ports = map_ciena_ports_in_line(line, allocator, model)
            mapped = f" mapped ports: {', '.join(mapped_ports)}" if mapped_ports else ""
            model.ring_comments.append(f"REVIEW Ciena 3920/3930 ring/protection command{mapped}: {line}")
        elif re.match(r"vlan\s+(create|add)\s+\d+", lower):
            vlan_id = re.findall(r"\d+", line)[0]
            name_match = re.search(r'name\s+"?([^"]+)"?', line, re.I)
            model.vlans[vlan_id] = normalize_vlan_name(name_match.group(1) if name_match else None, vlan_id)
        elif re.search(r"\bvlan\s+\d+\s+(create|name|description)\b", lower):
            vlan_id = re.search(r"\bvlan\s+(\d+)", lower).group(1)  # type: ignore[union-attr]
            name_match = re.search(r'(?:name|description)\s+"?([^"]+)"?', line, re.I)
            model.vlans[vlan_id] = normalize_vlan_name(name_match.group(1) if name_match else None, vlan_id)
        elif lower.startswith(("mstp ", "rstp ", "spanning-tree ", "stp ")):
            model.review_comments.append(f"REVIEW Ciena spanning-tree command: {line}")
        elif re.match(r"port\s+set\s+port\s+\S+", lower):
            source = re.search(r"port\s+set\s+port\s+(\S+)", line, re.I).group(1)  # type: ignore[union-attr]
            current_port = model.ports.setdefault(source, PortConfig(source_name=source, target_name=allocator.get(source)))
            parse_ciena_port_set_line(line, current_port, model)
        elif re.match(r"(interface|port)\s+\S+", lower):
            source = line.split()[1]
            current_port = model.ports.setdefault(source, PortConfig(source_name=source, target_name=allocator.get(source)))
        elif current_port and "description" in lower:
            current_port.description = line.split("description", 1)[1].strip().strip('"')
        elif current_port and ("untagged" in lower or "access" in lower) and re.search(r"\bvlan\s+\d+", lower):
            vlan_id = re.findall(r"vlan\s+(\d+)", lower)[0]
            current_port.mode = "access"
            current_port.access_vlan = vlan_id
            model.vlans.setdefault(vlan_id, f"VLAN_{vlan_id}")
        elif current_port and ("tagged" in lower or "trunk" in lower) and re.search(r"\bvlan\s+[\d,\-]+", lower):
            vlan_text = re.search(r"vlan\s+([\d,\-]+)", lower).group(1)  # type: ignore[union-attr]
            current_port.mode = "trunk"
            current_port.trunk_vlans = dedupe(current_port.trunk_vlans + expand_vlan_list(vlan_text))
        elif lower.startswith(("ip route ", "route add ")):
            parts = line.split()
            if len(parts) >= 4:
                model.static_routes.append((parts[-3], parts[-1], None))
        elif lower.startswith(("snmp-server ", "snmp ")):
            model.review_comments.append(f"REVIEW Ciena SNMP command: {line}")
        elif line:
            model.review_comments.append(f"REVIEW Ciena command: {line}")
    warnings.append("Ciena 3920/3930 syntax varies by SAOS release; generated L2/L3 basics should be reviewed against the original device output.")
    if model.ring_comments:
        warnings.append("Ciena ring-protection/ERPS/RAPS commands were detected. EX3400 does not receive a blind ring translation; validate whether the target design should use RSTP, MSTP, ERPS-capable equipment, or routed redundancy.")
    return model


def is_ciena_ring_line(lower: str) -> bool:
    ring_terms = (
        "ring-protection",
        "ethernet-ring",
        "erps",
        "r-aps",
        "raps",
        "logical-ring",
        "virtual-ring",
        "ring port",
        "ring-port",
        "east-port",
        "west-port",
        "control-vlan",
        "rpl",
        "wait-to-restore",
        "guard-timer",
        "holdoff",
    )
    return any(term in lower for term in ring_terms)


def map_ciena_ports_in_line(line: str, allocator: PortAllocator, model: ConfigModel) -> list[str]:
    mapped: list[str] = []
    patterns = [
        r"\b(?:port|interface|east-port|west-port|ring-port)\s+([A-Za-z]?\d+(?:/\d+)*)",
        r"\b(?:port|interface|east-port|west-port|ring-port)\s+([A-Za-z]+\d+/\d+/\d+)",
    ]
    for pattern in patterns:
        for source in re.findall(pattern, line, flags=re.I):
            port = model.ports.setdefault(source, PortConfig(source_name=source, target_name=allocator.get(source)))
            mapped.append(f"{port.source_name}->{port.target_name}")
    return dedupe(mapped)


def parse_ciena_port_set_line(line: str, port: PortConfig, model: ConfigModel) -> None:
    lower = line.lower()
    desc_match = re.search(r'description\s+"?([^"]+)"?', line, re.I)
    if desc_match:
        port.description = desc_match.group(1).strip()
    if "disable" in lower or "admin-state disabled" in lower:
        port.disabled = True
    vlan_match = re.search(r"\bvlan\s+([\d,\-]+)", lower)
    if vlan_match and ("untagged" in lower or "access" in lower):
        vlan_id = vlan_match.group(1).split(",", 1)[0]
        port.mode = "access"
        port.access_vlan = vlan_id
        model.vlans.setdefault(vlan_id, f"VLAN_{vlan_id}")
    elif vlan_match and ("tagged" in lower or "trunk" in lower):
        port.mode = "trunk"
        port.trunk_vlans = dedupe(port.trunk_vlans + expand_vlan_list(vlan_match.group(1)))
        for vlan_id in port.trunk_vlans:
            model.vlans.setdefault(vlan_id, f"VLAN_{vlan_id}")


def parse_generic_lines(lines: list[str], model: ConfigModel) -> None:
    for line in lines:
        parse_common_cisco_line(line.strip(), model)


def render_ex3400(model: ConfigModel, warnings: list[str], hostname_suffix: str) -> tuple[list[str], list[str]]:
    lines: list[str] = []
    notes = [
        "Target platform: Juniper EX3400-24P using ELS ethernet-switching syntax.",
        "Port assignment is automatic and shown in the app's mapping table.",
    ]
    hostname = model.hostname + hostname_suffix if model.hostname else "EX3400-CONVERTED"

    add(lines, f"set system host-name {hostname}")
    if model.router_id:
        add(lines, f"set routing-options router-id {model.router_id}")
    add(lines, "set system services ssh")
    add(lines, "set system services netconf ssh")
    add(lines, "set protocols lldp interface all")
    add(lines, "set protocols lldp-med interface all")
    add(lines, "set protocols rstp interface all")

    for user, privilege in model.users:
        cls = "super-user" if privilege in {"15", "admin"} else "operator"
        add(lines, f"set system login user {user} class {cls}")
        warnings.append(f"User {user} was created without a password. Add an encrypted-password before commit.")

    for vlan_id in sorted(model.vlans, key=lambda x: int(x) if x.isdigit() else 999999):
        vlan_name = normalize_vlan_name(model.vlans[vlan_id], vlan_id)
        add(lines, f"set vlans {vlan_name} vlan-id {vlan_id}")

    for port in model.ports.values():
        render_port(lines, port, model, warnings)

    for prefix, next_hop, preference in model.static_routes:
        if next_hop == "discard":
            cmd = f"set routing-options static route {prefix} discard"
        else:
            cmd = f"set routing-options static route {prefix} next-hop {next_hop}"
        if preference:
            cmd += f" preference {preference}"
        add(lines, cmd)

    for community, permission in model.snmp_communities:
        auth = "read-write" if permission and permission.upper() == "RW" else "read-only"
        add(lines, f"set snmp community {community} authorization {auth}")
    for host in dedupe(model.snmp_hosts):
        add(lines, f"set snmp trap-group converted-targets targets {host}")
    if model.snmp_location:
        add(lines, f"set snmp location \"{model.snmp_location}\"")
    if model.snmp_contact:
        add(lines, f"set snmp contact \"{model.snmp_contact}\"")

    for server in dedupe(model.ntp_servers):
        add(lines, f"set system ntp server {server}")
    for host in dedupe(model.syslog_hosts):
        add(lines, f"set system syslog host {host} any any")

    for area, iface in model.ospf_interfaces:
        add(lines, f"set protocols ospf area {area} interface {iface}")
    interface_area = {iface: area for area, iface in model.ospf_interfaces}
    for area, iface, option, value in model.ospf_interface_options:
        resolved_area = interface_area.get(iface, "0.0.0.0" if area == "__lookup__" else area)
        add(lines, f"set protocols ospf area {resolved_area} interface {iface} {option} {value}")
    for network, wildcard, area in model.ospf_networks:
        warnings.append(f"Cisco OSPF network {network} {wildcard} area {area} needs interface-level verification on Junos.")
        add(lines, f"# REVIEW set protocols ospf area {area} interface <matching-interface-for-{network}>")

    for preserved in model.extra_set_lines:
        add(lines, normalize_junos_ex3400_line(preserved))
    for bgp_line in model.bgp_lines:
        add(lines, bgp_line)
    for comment in model.ring_comments:
        add(lines, f"# {comment}")
    for comment in model.review_comments:
        add(lines, f"# {comment}")

    return dedupe(lines), notes


def render_port(lines: list[str], port: PortConfig, model: ConfigModel, warnings: list[str]) -> None:
    target = port.target_name
    if "." in target and target.startswith("irb."):
        unit = target.split(".", 1)[1]
        for address in port.ip_addresses:
            add(lines, f"set interfaces irb unit {unit} family inet address {address}")
        return

    if port.description:
        add(lines, f"set interfaces {target} description \"{port.description}\"")
    if port.disabled:
        add(lines, f"set interfaces {target} disable")
    if port.mtu:
        add(lines, f"set interfaces {target} mtu {port.mtu}")
    if port.lacp_group:
        ae = f"ae{port.lacp_group}"
        add(lines, f"set chassis aggregated-devices ethernet device-count 64")
        add(lines, f"set interfaces {target} ether-options 802.3ad {ae}")
        add(lines, f"set interfaces {ae} aggregated-ether-options lacp active")
        warnings.append(f"Mapped source LAG group {port.lacp_group} to {ae}; verify member count and remote LACP mode.")
        target = ae

    if port.ip_addresses:
        for address in port.ip_addresses:
            add(lines, f"set interfaces {target} unit 0 family inet address {address}")
    elif port.mode == "access" or port.access_vlan:
        vlan = port.access_vlan or "1"
        name = normalize_vlan_name(model.vlans.get(vlan), vlan)
        add(lines, f"set interfaces {target} unit 0 family ethernet-switching interface-mode access")
        add(lines, f"set interfaces {target} unit 0 family ethernet-switching vlan members {name}")
    elif port.mode == "trunk" or port.trunk_vlans:
        add(lines, f"set interfaces {target} unit 0 family ethernet-switching interface-mode trunk")
        members = port.trunk_vlans or ["all"]
        for vlan in members:
            if vlan == "all":
                add(lines, f"set interfaces {target} unit 0 family ethernet-switching vlan members all")
            else:
                name = normalize_vlan_name(model.vlans.get(vlan), vlan)
                add(lines, f"set interfaces {target} unit 0 family ethernet-switching vlan members {name}")
        if port.native_vlan:
            native_name = normalize_vlan_name(model.vlans.get(port.native_vlan), port.native_vlan)
            add(lines, f"set interfaces {target} native-vlan-id {port.native_vlan}")
            add(lines, f"set interfaces {target} unit 0 family ethernet-switching vlan members {native_name}")

    for comment in port.extra_comments:
        add(lines, f"# {target}: {comment}")


def normalize_junos_ex3400_line(line: str) -> str:
    line = line.replace(" family ethernet-switching port-mode ", " family ethernet-switching interface-mode ")
    line = line.replace("set interfaces vlan unit", "set interfaces irb unit")
    line = re.sub(r"set vlans (\S+) l3-interface vlan\.(\d+)", r"set vlans \1 l3-interface irb.\2", line)
    line = re.sub(r"(set protocols ospf area \S+ interface )vlan\.(\d+)", r"\1irb.\2", line)
    return line


def add(lines: list[str], line: str) -> None:
    if line and line not in lines:
        lines.append(line)


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert legacy configs to Juniper EX3400-24P Junos set commands.")
    parser.add_argument("input", nargs="?", help="Source config file. If omitted, paste config on stdin.")
    parser.add_argument("-p", "--platform", default="auto", choices=SUPPORTED_PLATFORMS.keys())
    parser.add_argument("-s", "--stack-members", default=1, type=int)
    parser.add_argument("-t", "--target-model", default="ex3400_24p", choices=TARGET_MODELS.keys())
    parser.add_argument("-o", "--output", help="Write converted config to this file.")
    args = parser.parse_args()

    if args.input:
        with open(args.input, "r", encoding="utf-8") as handle:
            raw = handle.read()
    else:
        print("Paste the source config, then press Ctrl+Z and Enter on Windows or Ctrl+D on Linux/macOS:")
        import sys

        raw = sys.stdin.read()

    result = convert_config(raw, args.platform, args.stack_members, target_model=args.target_model)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(result.config)
    else:
        print(result.config)
    if result.warnings:
        print("\nWarnings:")
        for warning in result.warnings:
            print(f"- {warning}")


if __name__ == "__main__":
    main()
