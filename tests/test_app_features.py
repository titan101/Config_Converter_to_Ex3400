import io
import zipfile

from app import (
    app,
    apply_port_overrides,
    apply_template_profile,
    build_mapping_csv,
    build_report,
    parse_port_overrides,
)
from converter import convert_config


def test_port_overrides_rewrite_config_and_mapping():
    result = convert_config(
        """
hostname SW1
interface GigabitEthernet1/0/1
 switchport mode access
 switchport access vlan 10
""",
        "cisco_ios",
        1,
    )
    overrides = parse_port_overrides("GigabitEthernet1/0/1 = ge-0/0/10")
    updated = apply_port_overrides(result, overrides)
    assert updated.source_to_target_ports["GigabitEthernet1/0/1"] == "ge-0/0/10"
    assert "set interfaces ge-0/0/10 unit 0 family ethernet-switching" in updated.config
    assert "ge-0/0/0 unit 0 family ethernet-switching" not in updated.config


def test_template_profile_and_report_validation():
    config = "set interfaces irb unit 100 family inet address 10.0.0.1/24\n"
    profiled = apply_template_profile(config, "basic_ops")
    assert "set system syslog file messages any notice" in profiled
    report = build_report(profiled, [], {}, "test")
    assert report["counts"]["irbs"] == 1
    assert any("no VLAN l3-interface binding" in item["message"] for item in report["validations"])


def test_mapping_csv_output():
    csv_text = build_mapping_csv({"old1": "ge-0/0/0"})
    assert csv_text.splitlines() == ["source_port,target_port", "old1,ge-0/0/0"]


def test_batch_convert_endpoint_returns_zip():
    client = app.test_client()
    data = {
        "platform": "cisco_ios",
        "target_model": "ex3400_24p",
        "stack_members": "1",
        "hostname_suffix": "-EX3400",
        "config_files": (io.BytesIO(b"hostname SW1\n"), "sw1.txt"),
    }
    response = client.post("/batch", data=data, content_type="multipart/form-data")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        names = set(archive.namelist())
        assert "sw1/converted_ex3400.conf" in names
        assert "sw1/migration_report.json" in names
        assert "batch_index.json" in names
        assert "set system host-name SW1-EX3400" in archive.read("sw1/converted_ex3400.conf").decode()
