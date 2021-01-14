# >>>OVERVIEW
# This script will use a text file described in the router_list variable below
# that contains a list of predefined Juniper devices and BGP peers.  The script
# will then attempt to connect to them using the NETCONF over SSH protocol to
# issue RPC calls for the advertised prefixes of those peers.
#
# >>>OUTPUT
# The script will write 3 types of files:
#
# 1) w.x.y.z-prefixes.csv - contains a list of all prefixes for that peer
#
# 2) allPrefixPeers.csv - contains all unique prefixes and what peers they are advertised to
#
# 3) allSortedByPrefix.csv - contains all peers and all prefixes, sorted by prefix
#
# If you wish to save these files, transfer them away from the script directory
# before you run the script again, otherwise they will be overwritten.
#
# >>>HOW TO USE
# To run:
#
# python / python3 get-advertised-prefixes.py
#
# >>>REQUIREMENTS
# Local executing environment:
# 1) Python 3
# 2) pip / pip3
# 3) pip / pip3 install --upgrade pip
# 4) pip / pip3 install -r requirements.txt
# 5) A populated device text file in the script's directory as referenced by the router_list variable below.
#
# Remote device:
# 1) Juniper series router, switch, or firewall
# 2) An admin account to connect to the box
# 3) Each device you are connecting to must have NETCONF enabled
#    set system services netconf ssh
#    commit confirmed
#
import os
import sys
import pprint
import csv
import operator
import re
from jnpr.junos import Device
from getpass import getpass
from jnpr.junos.exception import ConnectError
from lxml import etree
from os import path

router_list = "juniper-bgp-routers.txt"


def node_values_list(xml_doc, xpath_expr):
    """Pull text elements using XPath
    """
    return [x.text for x in xml_doc.xpath(xpath_expr)]


def rpc_execute(rpc_call, hostname, username, password):
    """Standard RPC caller
    """
    try:
        with Device(host=hostname, user=username, passwd=password) as dev:
            rpc_return = dev.execute(rpc_call)
    except ConnectError as err:
        print("ERROR - cannot connect to {}, {}".format(hostname, err))
        sys.exit(1)
    except Exception as err:
        print(err)
        sys.exit(1)

    return rpc_return


def find_peers(prefix, sorted_list):
    """Given a presorted list, return all peers for a prefix
    """
    result = []
    for row in sorted_list:
        if row[0] == prefix:  # row[0] should be the prefix
            result.append(row[1])  # row[1] should be the peer IP
    return result


def parse_prefixes():
    """Provide multiple CSV output files for prefixes and peers
    """
    prefix_files = []
    for file in os.listdir():
        if "-prefixes.csv" in file:
            prefix_files.append(file)

    # I need a CSV file to help me sort things
    header = 0
    if os.path.exists('temp.csv'):
        os.remove('temp.csv')
    for file in prefix_files:
        with open(file, newline='') as file:
            reader = csv.DictReader(file)
            with open('temp.csv', 'a', newline='') as tempfile:
                writer = csv.DictWriter(
                    tempfile, ["Prefix", "Peer", "Active", "Protocol", "Nexthop", "AS Path"])
                if header == 0:
                    writer.writeheader()
                    header = 1
                writer.writerows(reader)
            for row in reader:
                print(row)

    # Output a CSV file that contains all prefixes and peers
    all_advertisements = csv.reader(open('temp.csv'))
    sorted_list = sorted(all_advertisements,
                         key=operator.itemgetter(0), reverse=True)
    with open('allSortedByPrefix.csv', 'w', newline='') as tempfile:
        writer = csv.writer(tempfile)
        for row in sorted_list:
            writer.writerow(row)

    # Find the uniques
    unique_peers = []
    unique_prefixes = []
    for row in sorted_list:
        if row[1] not in unique_peers:  # row[1] should be the peer
            if row[1] != "Peer":
                unique_peers.append(row[1])
        if row[0] not in unique_prefixes:  # row[0] should be the prefix
            if row[0] != "Prefix":
                unique_prefixes.append(row[0])

    print("//" * 40)

    # Output a CSV file that contains each prefix (one per line), with the matching peer(s) they are advertised to
    with open('allPrefixPeers.csv', 'w', newline='') as file:
        writer = csv.writer(file)
        header = ["Prefix", "Peers"]
        writer.writerow(header)
        all_prefix_totals = 0
        print("Prefix list")
        for prefix in unique_prefixes:
            cur_peers = find_peers(prefix, sorted_list)
            print("Prefix: {} --> {}".format(prefix, ', '.join(cur_peers)))
            row = [[prefix] + cur_peers]
            writer.writerows(row)
            all_prefix_totals += 1
        print("Total global prefixes: " + str(all_prefix_totals))
    return


def input_file_check(router_list):
    """Enable a basic input file check
    """
    if os.path.exists(router_list):
        print("Found device file ({}): OK".format(router_list))
    else:
        print('''
Unable to find device list >>>{}<<<, please verify it exists and/or update the
variable ___router_list___ at the top of this script file to point to a new one.

Script error, exiting.'''.format(router_list))
        sys.exit(1)

    with open(router_list) as f:
        for line in f:
            if ";" not in line:
                if "r" in line:
                    if "p" in line:
                        print("Processing line:", line.strip())
                    else:
                        print("ERROR with line:", line.strip())
                        print('''
Your {} file may contain invalid entries, please double check it.

Examples:

One Juniper router with one peer
r10.10.10.10, p3.3.3.3

Two Juniper routers, one with one peer, the other with multiple
r10.20.30.40, p4.4.4.4
r192.168.1.22, p5.5.5.5, p6.6.6.6, p7.7.7.7

'''.format(router_list))
                        sys.exit(1)

    print("Line check: OK")
    return


def main():

    input_file_check(router_list)

    print('''
This script connects to each device in the supplied file: >>>''' + router_list + '''<<< 
using credentials you supply below.  If your creds are different for each device, 
this script won't work and you should ___abort now___.

At run time we collect a list of advertised prefixes for the BGP protocol using
EXTERNAL peer sessions.  That information is displayed on the screen as well as
written to files in the directory that this script is executed from.
    ''')
    username = input("Username: ")
    password = getpass("Password: ")
    with open(router_list) as f:
        for line in f:
            if ";" not in line:
                line_items = line.split(',')
                hostname = line_items[0].replace('r', '')
                external_peers = []
                for peer in line_items:
                    if "p" in peer:
                        external_peers.append(peer.replace(
                            'p', '').replace('\n', '').replace(' ', ''))
                print("//" * 40)
                print("Connecting to: {} ...".format(hostname))

                # Get prefixes from each External BGP Peer
                all_peer_prefixes = {}
                for bgp_peer in external_peers:
                    print("Processing BGP peer {} ...".format(bgp_peer))

                    rpc = '''
                    <get-route-information>
                    <advertising-protocol-name>bgp</advertising-protocol-name>
                    <neighbor>''' + bgp_peer + '''</neighbor>
                    </get-route-information>
                    '''

                    all_peer_prefixes[bgp_peer] = rpc_execute(
                        rpc, hostname, username, password)

                # Process prefixes for each peer
                xpath_rt = '//rt-destination'
                xpath_active = '//active-tag'
                xpath_protocol = '//protocol-name'
                xpath_as = '//as-path'
                xpath_nexthop = '//to'

                peer_index = 0
                for peer in all_peer_prefixes:
                    all_prefixes = {}
                    print("Peer: " + peer)

                    prefixes = node_values_list(
                        all_peer_prefixes[peer], xpath_rt)
                    prefixes_active = node_values_list(
                        all_peer_prefixes[peer], xpath_active)
                    prefixes_protocol = node_values_list(
                        all_peer_prefixes[peer], xpath_protocol)
                    prefixes_as = node_values_list(
                        all_peer_prefixes[peer], xpath_as)
                    prefixes_next_hop = node_values_list(
                        all_peer_prefixes[peer], xpath_nexthop)
                    for prefix_index in range(0, len(prefixes)):
                        all_prefixes[prefix_index] = {
                            "prefix": prefixes[prefix_index],
                            "active": prefixes_active[prefix_index],
                            "protocol": prefixes_protocol[prefix_index],
                            "AS": prefixes_as[prefix_index].replace('\n', ''),
                            "nexthop": prefixes_next_hop[prefix_index]
                        }
                    peer_index += 1
                    prefix_total = 0
                    print("{:2}{:25}{:10}{:15}{:15}".format(
                        " ", "Prefix", "Protocol", "Next Hop", "AS Path"))
                    for index in all_prefixes:
                        print("{:2}{:25}{:10}{:15}{:15}".format(
                            all_prefixes[index]["active"],
                            all_prefixes[index]["prefix"],
                            all_prefixes[index]["protocol"],
                            all_prefixes[index]["nexthop"],
                            all_prefixes[index]["AS"]
                        ))
                        prefix_total += 1
                    print("Prefix total: " + str(prefix_total))
                    # For each peer we are going to output a CSV with all prefixes
                    cur_file = peer + "-prefixes.csv"
                    with open(cur_file, 'w', newline='') as file:
                        writer = csv.writer(file)
                        header = ["Peer", "Active", "Prefix",
                                  "Protocol", "Nexthop", "AS Path"]
                        writer.writerow(header)
                        for index in all_prefixes:
                            row = []
                            row = peer, all_prefixes[index]["active"], all_prefixes[index]["prefix"], all_prefixes[
                                index]["protocol"], all_prefixes[index]["nexthop"], all_prefixes[index]["AS"]
                            writer.writerow(row)

        # After NETCONF is complete, go through and output more files
        parse_prefixes()
        print("//" * 40)
        print("Script complete.")
        print('''
The script wrote 3 types of files for you in this directory:
1) w.x.y.z-prefixes.csv - contains a list of all prefixes for that peer
2) allPrefixPeers.csv - contains all unique prefixes and what peers they are advertised to
3) allSortedByPrefix.csv - contains all peers and all prefixes, sorted by prefix
        ''')
        # Temp file cleanup
        if os.path.exists('temp.csv'):
                os.remove('temp.csv')


if __name__ == "__main__":
   main()
