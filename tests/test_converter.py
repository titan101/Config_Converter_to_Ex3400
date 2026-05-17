from converter import convert_config


def test_cisco_access_trunk_svi_and_static_route():
    source = """
hostname SW1
vlan 10
 name USERS
vlan 20
 name VOICE
interface GigabitEthernet1/0/1
 description User jack
 switchport mode access
 switchport access vlan 10
interface GigabitEthernet1/0/2
 switchport mode trunk
 switchport trunk native vlan 10
 switchport trunk allowed vlan 10,20
interface Vlan10
 ip address 10.10.10.1 255.255.255.0
ip route 0.0.0.0 0.0.0.0 10.10.10.254
snmp-server community public RO
ntp server 192.0.2.10
"""
    result = convert_config(source, "cisco_ios", 1)
    assert "set vlans USERS vlan-id 10" in result.config
    assert "set interfaces ge-0/0/0 unit 0 family ethernet-switching interface-mode access" in result.config
    assert "set interfaces ge-0/0/1 unit 0 family ethernet-switching interface-mode trunk" in result.config
    assert "set interfaces ge-0/0/1 native-vlan-id 10" in result.config
    assert "set interfaces irb unit 10 family inet address 10.10.10.1/24" in result.config
    assert "set routing-options static route 0.0.0.0/0 next-hop 10.10.10.254" in result.config
    assert "set snmp community public authorization read-only" in result.config


def test_junos_legacy_port_mode_and_rvi_translation():
    source = """
set system host-name EX3300-OLD
set vlans DATA vlan-id 100
set vlans DATA l3-interface vlan.100
set interfaces ge-0/0/1 unit 0 family ethernet-switching port-mode trunk
set interfaces ge-0/0/1 unit 0 family ethernet-switching vlan members DATA
set interfaces vlan unit 100 family inet address 10.100.0.1/24
"""
    result = convert_config(source, "juniper_ex3300", 1)
    assert "set interfaces ge-0/0/1 unit 0 family ethernet-switching interface-mode trunk" in result.config
    assert "set interfaces irb unit 100 family inet address 10.100.0.1/24" in result.config
    assert "set vlans DATA l3-interface irb.100" in result.config


def test_stack_port_auto_assignment_crosses_member_boundary():
    source = "\n".join(
        f"interface GigabitEthernet1/0/{i}\n switchport access vlan 10\n!"
        for i in range(1, 27)
    )
    result = convert_config(source, "cisco_6500", 2)
    assert result.source_to_target_ports["GigabitEthernet1/0/1"] == "ge-0/0/0"
    assert result.source_to_target_ports["GigabitEthernet1/0/25"] == "ge-1/0/0"


def test_ciena_3920_3930_ring_lines_are_preserved_for_review():
    source = """
system set name CIENA-3930-A
vlan create 100 name CUST_A
port set port 1 description "Ring east"
port set port 1 tagged vlan 100
ring-protection logical-ring create ring-1 east-port 1 west-port 2 control-vlan 4094
erps ring 1 rpl owner port 1
"""
    result = convert_config(source, "ciena", 1)
    assert "set system host-name CIENA-3930-A-EX3400" in result.config
    assert "set vlans CUST_A vlan-id 100" in result.config
    assert "set interfaces ge-0/0/0 description \"Ring east\"" in result.config
    assert "set interfaces ge-0/0/0 unit 0 family ethernet-switching interface-mode trunk" in result.config
    assert "# REVIEW Ciena 3920/3930 ring/protection command" in result.config
    assert any("ring-protection/ERPS/RAPS" in warning for warning in result.warnings)


def test_checkpoint_gaia_conversion_from_shell_script_patterns():
    source = """
set hostname CP-GW1
set interface lo ipv4-address 192.0.2.1 mask-length 32
set interface Mgmt ipv4-address 10.0.0.10 mask-length 24
set interface Mgmt comments "Management"
set interface eth1 comments "inside"
set interface eth1 mtu 1500
add interface eth1 vlan 100
set interface eth1.100 ipv4-address 172.16.100.1 mask-length 24
set static-route default nexthop gateway address 10.0.0.1 on
set ospf instance default interface eth1 area backbone on
set ospf instance default interface eth1 cost 20
set bgp external remote-as 65001 peer 203.0.113.2 on
"""
    result = convert_config(source, "checkpoint", 1)
    assert "set system host-name CP-GW1-EX3400" in result.config
    assert "set interfaces lo0 unit 0 family inet address 192.0.2.1/32" in result.config
    assert "set interfaces me0 unit 0 family inet address 10.0.0.10/24" in result.config
    assert "set interfaces ge-0/0/0 description \"inside\"" in result.config
    assert "set vlans V100 vlan-id 100" in result.config
    assert "set interfaces irb unit 100 family inet address 172.16.100.1/24" in result.config
    assert "set routing-options static route 0.0.0.0/0 next-hop 10.0.0.1" in result.config
    assert "set protocols ospf area 0.0.0.0 interface ge-0/0/0.0 metric 20" in result.config
    assert "set protocols bgp group EBGP neighbor 203.0.113.2 peer-as 65001" in result.config


def test_brocade_conversion_from_shell_script_patterns():
    source = """
hostname BR1
ip router-id 192.0.2.10
vlan 100 name USERS
 tagged ethe 1 to 2
 untagged ethe 25
 router-interface ve 100
!
interface ethernet 1
 port-name Uplink Core
!
interface ve 100
 ip address 10.100.0.1 255.255.255.0
 ip ospf area 0.0.0.0
 ip ospf cost 5
!
ip route 0.0.0.0 0.0.0.0 10.100.0.254
ip route 198.51.100.0 255.255.255.0 null0
"""
    result = convert_config(source, "brocade", 2)
    assert "set system host-name BR1-EX3400" in result.config
    assert "set routing-options router-id 192.0.2.10" in result.config
    assert "set vlans USERS vlan-id 100" in result.config
    assert "set interfaces ge-0/0/0 description \"Uplink_Core\"" in result.config
    assert "set interfaces ge-0/0/0 unit 0 family ethernet-switching interface-mode trunk" in result.config
    assert "set interfaces ge-1/0/0 unit 0 family ethernet-switching interface-mode access" in result.config
    assert "set interfaces irb unit 100 family inet address 10.100.0.1/24" in result.config
    assert "set protocols ospf area 0.0.0.0 interface irb.100 metric 5" in result.config
    assert "set routing-options static route 198.51.100.0/24 discard" in result.config


def test_ex3200_ospf_vlan_interface_maps_to_irb():
    source = """
set system host-name EX3200-A
set vlans DATA vlan-id 200
set interfaces vlan unit 200 family inet address 10.200.0.1/24
set protocols ospf area 0.0.0.0 interface vlan.200
"""
    result = convert_config(source, "juniper_ex3200", 1)
    assert "set interfaces irb unit 200 family inet address 10.200.0.1/24" in result.config
    assert "set protocols ospf area 0.0.0.0 interface irb.200" in result.config


def test_ex4200_48_port_source_maps_to_24p_stack():
    source = """
set system host-name EX4200-A
set vlans USERS vlan-id 10
set interfaces ge-0/0/24 description over_twenty_four
set interfaces ge-0/0/24 unit 0 family ethernet-switching port-mode access
set interfaces ge-0/0/24 unit 0 family ethernet-switching vlan members USERS
"""
    result = convert_config(source, "juniper_ex4200", 2, target_model="ex3400_24p_stack")
    assert result.source_to_target_ports["ge-0/0/24"] == "ge-1/0/0"
    assert "set interfaces ge-1/0/0 description \"over_twenty_four\"" in result.config
    assert "set interfaces ge-1/0/0 unit 0 family ethernet-switching interface-mode access" in result.config


def test_ex4200_preserves_48_port_number_on_48_port_target():
    source = """
set vlans USERS vlan-id 10
set interfaces ge-0/0/47 unit 0 family ethernet-switching port-mode access
set interfaces ge-0/0/47 unit 0 family ethernet-switching vlan members USERS
"""
    result = convert_config(source, "juniper_ex4200", 1, target_model="ex3400_48")
    assert result.source_to_target_ports["ge-0/0/47"] == "ge-0/0/47"
    assert "set interfaces ge-0/0/47 unit 0 family ethernet-switching interface-mode access" in result.config


def test_ex4500_ports_are_auto_assigned_to_access_ports():
    source = """
set interfaces xe-0/0/0 description old_core
set interfaces xe-0/0/0 unit 0 family ethernet-switching port-mode trunk
"""
    result = convert_config(source, "juniper_ex4500", 1)
    assert result.source_to_target_ports["xe-0/0/0"] == "ge-0/0/0"
    assert "set interfaces ge-0/0/0 description \"old_core\"" in result.config
    assert "set interfaces ge-0/0/0 unit 0 family ethernet-switching interface-mode trunk" in result.config


def test_mx104_ports_are_auto_assigned_and_router_lines_are_preserved():
    source = """
set system host-name MX104-EDGE
set chassis hardware-model mx104
set interfaces xe-2/0/0 description uplink
set interfaces xe-2/0/0 unit 0 family inet address 203.0.113.2/30
set routing-options static route 0.0.0.0/0 next-hop 203.0.113.1
set protocols ospf area 0.0.0.0 interface xe-2/0/0.0
"""
    result = convert_config(source, "juniper_mx104", 1)
    assert result.source_to_target_ports["xe-2/0/0"] == "ge-0/0/0"
    assert "set interfaces ge-0/0/0 description \"uplink\"" in result.config
    assert "set interfaces ge-0/0/0 unit 0 family inet address 203.0.113.2/30" in result.config
    assert "set routing-options static route 0.0.0.0/0 next-hop 203.0.113.1" in result.config
    assert any("MX104" in warning for warning in result.warnings)


def test_mx104_auto_detection():
    source = "set system host-name mx104-edge\nset chassis hardware-model mx104\n"
    result = convert_config(source, "auto", 1)
    assert result.platform == "juniper_mx104"


def test_mx204_hierarchical_junos_config_flattens_and_converts():
    source = """\ufeff
system {
    host-name MX204-LAB-INET-EDGE-01;
    login {
        user netops {
            class super-user;
        }
    }
    services {
        ssh {
            protocol-version v2;
        }
        netconf {
            ssh;
        }
    }
    name-server {
        192.0.2.53;
    }
}
interfaces {
    lo0 {
        unit 0 {
            family inet {
                address 10.255.0.11/32;
            }
        }
    }
    xe-0/0/0 {
        description "CORE-LINK-1";
        unit 0 {
            family inet {
                address 198.51.100.11/31;
            }
            family mpls;
        }
    }
}
snmp {
    community LAB-RO {
        authorization read-only;
    }
}
routing-options {
    router-id 10.255.0.11;
    autonomous-system 65011;
}
policy-options {
    prefix-list CUSTOMER-PREFIXES {
        203.0.113.0/24;
    }
}
protocols {
    bgp {
        group TRANSIT-V4 {
            type external;
            peer-as 64496;
            neighbor 203.0.113.1 {
                description "TRANSIT-A-V4";
            }
        }
    }
}
firewall {
    family inet {
        filter PROTECT-RE {
            term DEFAULT-DENY {
                then discard;
            }
        }
    }
}
"""
    result = convert_config(source, "auto", 1)
    assert result.platform == "juniper_mx204"
    assert result.source_to_target_ports["xe-0/0/0"] == "ge-0/0/0"
    assert "set system host-name MX204-LAB-INET-EDGE-01-EX3400" in result.config
    assert "set system login user netops class super-user" in result.config
    assert "set system services ssh protocol-version v2" in result.config
    assert "set system name-server 192.0.2.53" in result.config
    assert "set interfaces lo0 unit 0 family inet address 10.255.0.11/32" in result.config
    assert "set interfaces ge-0/0/0 description \"CORE-LINK-1\"" in result.config
    assert "set interfaces ge-0/0/0 unit 0 family inet address 198.51.100.11/31" in result.config
    assert "set snmp community LAB-RO authorization read-only" in result.config
    assert "set routing-options router-id 10.255.0.11" in result.config
    assert "set routing-options autonomous-system 65011" in result.config
    assert "set policy-options prefix-list CUSTOMER-PREFIXES 203.0.113.0/24" in result.config
    assert "set protocols bgp group TRANSIT-V4 neighbor 203.0.113.1 description \"TRANSIT-A-V4\"" in result.config
    assert "# REVIEW source Junos platform-specific command: set firewall family inet filter PROTECT-RE term DEFAULT-DENY then discard" in result.config
    assert any("MX204" in warning for warning in result.warnings)
