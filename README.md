# Config_Converter_to_Ex3400

Local Python app for converting pasted legacy network configurations into Juniper EX3400 Junos `set` commands.

## Run

### Windows

Double-click:

```text
Launch_Config_Convert_App.bat
```

The launcher checks dependencies, starts the local server, and opens the browser.

Manual run:

```powershell
cd path\to\Config_Converter_to_Ex3400
python -m pip install -r requirements.txt
python app.py
```

Open:

```text
http://127.0.0.1:5050
```

### WSL / Linux

From the project directory:

```bash
chmod +x run_wsl.sh
./run_wsl.sh
```

Then open:

```text
http://127.0.0.1:5050
```

If your WSL environment does not support Python virtual environments, use:

```bash
chmod +x run_wsl_no_venv.sh
./run_wsl_no_venv.sh
```

Manual WSL run:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

Docker run:

```bash
docker compose up --build
```

## App Features

- Dark-mode web UI
- Source samples for quick validation
- Built-in sample configs for Cisco, Brocade, Check Point, EX3200, EX4200, and M7i
- Target model, stack member, and hostname suffix controls
- Pure Junos `set` command output
- Optional `load set terminal` helper comments above the set commands
- Default-on secret redaction for SNMP communities, authentication keys, passwords, and secrets
- Optional hiding of `# REVIEW` comments
- Copy and download actions for generated configs
- Output counts for set lines, review lines, warnings, and port mappings
- Batch upload that exports a zip with converted configs, mapping CSVs, JSON reports, and optional source files
- Port mapping overrides such as `old_port = ge-0/0/10`
- Migration report with counts, validation findings, and review categories
- Mapping CSV and report JSON downloads
- Source-to-output matched view for quick review
- Standard profile injection for common ops/turn-up baseline settings
- JSON automation endpoints for platforms, conversion, and validation
- Docker and Docker Compose support for WSL/Linux/containerized runs
- GitHub Actions CI for tests and compile checks

## CLI

```powershell
python converter.py old_switch.txt --platform cisco_ios --stack-members 2 --target-model ex3400_24p_stack --output converted_ex3400.conf
```

WSL/Linux CLI:

```bash
python3 converter.py old_switch.txt --platform cisco_ios --stack-members 2 --target-model ex3400_24p_stack --output converted_ex3400.conf
```

## API

List supported inputs:

```bash
curl http://127.0.0.1:5050/api/platforms
```

Convert a config:

```bash
curl -X POST http://127.0.0.1:5050/api/convert \
  -H "Content-Type: application/json" \
  -d '{"platform":"cisco_ios","source_config":"hostname SW1\n","redact_secrets":true}'
```

Validate/report only:

```bash
curl -X POST http://127.0.0.1:5050/api/validate \
  -H "Content-Type: application/json" \
  -d '{"platform":"cisco_ios","source_config":"hostname SW1\n"}'
```

## What It Converts

- Cisco IOS and Catalyst 6500 basics: VLANs, access/trunk ports, native VLANs, SVIs, static routes, SNMP, NTP, syslog, users, LAG hints, and OSPF review placeholders.
- Check Point Gaia basics: hostname, loopback, Mgmt to `me0`, `ethN` interfaces, VLAN subinterfaces to `irb.N`, static routes, OSPF interface options, BGP external neighbors, and OSPF redistribution policy hints.
- Brocade/Ruckus ICX basics: VLAN names, tagged/untagged memberships, Ethernet port mapping, VE interfaces to IRB, OSPF area/cost, loopback, static routes including Null0 to discard, router ID, and descriptions.
- Legacy EX3200 Junos: `vlan.N` SVI and OSPF references are converted to `irb.N`.
- Legacy EX3300 Junos: VLANs, `port-mode` to ELS `interface-mode`, `vlan.N` RVI to `irb.N`, SNMP, static routes, system services, and compatible protocols.
- Legacy EX4200 Junos: EX4200 access-port configs map to EX3400-24P stacks or EX3400-48-port targets, with `port-mode` converted to ELS `interface-mode`.
- Legacy EX4500 Junos: EX4500 `xe-*` physical interfaces are auto-assigned to target EX3400 ports and flagged for optics/speed review.
- Juniper M7i Junos: compatible system, routing, SNMP, and interface address lines with physical ports auto-assigned to EX3400 ports.
- Juniper MX104 Junos: compatible routing, SNMP, and interface address lines with physical ports auto-assigned to EX3400 ports and MX-only feature warnings.
- Ciena 3920/3930 SAOS basics: VLANs, tagged/untagged port hints, static routes, hostname, and review comments for platform-specific lines.
- Ciena ring/protection detection: preserves `ring-protection`, ERPS, R-APS/RAPS, logical/virtual ring, east/west port, control VLAN, RPL, and timer-related lines as EX3400 review comments with any auto-mapped ports shown.

## Important

This is a migration assistant, not a blind paste-and-commit tool. Review every generated warning before loading the config on production equipment. Some source features do not exist on an EX3400-24P or require design choices that software cannot safely infer.

Secret redaction is enabled by default in the web app and API. Turn it off only when you intentionally need the generated output to preserve source secrets.

See `docs/research_recommendations.md` for the research-backed improvement list used to guide the current app structure.

This public repository is intended as a read-only published project. Open a fork for outside experiments rather than pushing directly to the main repository.

Ciena 3920/3930 ring configurations need extra design review. The app will detect and preserve ring commands, but it will not pretend that SAOS ring protection maps directly to an EX3400. Validate whether the replacement design should use RSTP, MSTP, a routed handoff, or equipment that supports the same ring protocol behavior.

Check Point firewall policy, NAT, VPN, clustering, anti-spoofing, and security blade settings are outside the EX3400 switching feature set and are not converted.

References used while shaping the Junos output:

- Juniper EX3400 hardware guide
- Juniper EX3400/EX virtual chassis configuration guide
- Juniper Junos bridging and VLAN documentation
- Juniper Junos SNMP documentation
- Juniper Junos OSPF documentation
