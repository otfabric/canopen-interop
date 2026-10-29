#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Write the EDS/DCF fixtures of fixtures/eds/ (docs/FIXTURES.md).

The files are committed; this script documents how they were made and
rewrites them byte for byte (`make fixtures-check` compares). Run it after
changing an object here, never edit the fixtures by hand.

    scripts/gen-eds-fixtures.py [--check]
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "fixtures", "eds")

U8, U16, U32, U64 = "0x0005", "0x0006", "0x0007", "0x001B"
I32, REAL32, VSTR, OSTR, DOMAIN = "0x0004", "0x0008", "0x0009", "0x000A", "0x000F"

# 64 characters: the capacity of 0x2004 (CANopenNode sizes a string by its default).
VISIBLE_STRING_DEFAULT = "canopen-interop visible string fixture 0123456789abcdefghijklmnop"[:64]
assert len(VISIBLE_STRING_DEFAULT) == 64


def var(name, dtype, access, default=None, pdo=False, low=None, high=None, extra=None):
    d = {"ParameterName": name, "ObjectType": "0x7", "DataType": dtype, "AccessType": access}
    if default is not None:
        d["DefaultValue"] = default
    d["PDOMapping"] = "1" if pdo else "0"
    if low is not None:
        d["LowLimit"] = low
    if high is not None:
        d["HighLimit"] = high
    if extra:
        d.update(extra)
    return d


class Obj:
    def __init__(self, index, name, otype="0x7", var_=None, subs=None, extra=None):
        self.index, self.name, self.otype = index, name, otype
        self.var, self.subs, self.extra = var_, subs or [], extra or {}

    def lines(self):
        out = ["[%X]" % self.index]
        if self.var is not None:
            d = dict(self.var)
            d["ParameterName"] = self.name
            out += ["%s=%s" % kv for kv in d.items()]
        else:
            out += ["ParameterName=%s" % self.name, "ObjectType=%s" % self.otype]
            out += ["%s=%s" % kv for kv in self.extra.items()]
            if self.subs:
                out.append("SubNumber=0x%X" % len(self.subs))
        out.append("")
        for sub, d in self.subs:
            out.append("[%Xsub%X]" % (self.index, sub))
            out += ["%s=%s" % kv for kv in d.items()]
            out.append("")
        return out


def record(index, name, subs, otype="0x9"):
    return Obj(index, name, otype, subs=subs)


def highest(n, access="ro"):
    return var("Highest sub-index supported", U8, access, "0x%02X" % n)


def rpdo(n, enabled, mapping):
    cob = 0x200 + 0x100 * (n - 1)
    cob_value = "$NODEID+0x%X" % cob if enabled else "$NODEID+0x%08X" % (0x80000000 | cob)
    comm = record(0x1400 + n - 1, "RPDO communication parameter %d" % n, [
        (0, highest(5)),
        (1, var("COB-ID used by RPDO", U32, "rw", cob_value)),
        (2, var("Transmission type", U8, "rw", "0xFF")),
        (5, var("Event timer", U16, "rw", "0")),
    ])
    maps = [(0, var("Number of mapped application objects in PDO", U8, "rw", "%d" % len(mapping)))]
    for i in range(1, 9):
        maps.append((i, var("Application object %d" % i, U32, "rw",
                            "0x%08X" % (mapping[i - 1] if i <= len(mapping) else 0))))
    return [comm, record(0x1600 + n - 1, "RPDO mapping parameter %d" % n, maps)]


def tpdo(n, enabled, mapping):
    cob = 0x180 + 0x100 * (n - 1)
    cob_value = "$NODEID+0x%X" % cob if enabled else "$NODEID+0x%08X" % (0x80000000 | cob)
    comm = record(0x1800 + n - 1, "TPDO communication parameter %d" % n, [
        (0, highest(6)),
        (1, var("COB-ID used by TPDO", U32, "rw", cob_value)),
        (2, var("Transmission type", U8, "rw", "0xFF")),
        (3, var("Inhibit time", U16, "rw", "0")),
        (5, var("Event timer", U16, "rw", "0")),
        (6, var("SYNC start value", U8, "rw", "0")),
    ])
    maps = [(0, var("Number of mapped application objects in PDO", U8, "rw", "%d" % len(mapping)))]
    for i in range(1, 9):
        maps.append((i, var("Application object %d" % i, U32, "rw",
                            "0x%08X" % (mapping[i - 1] if i <= len(mapping) else 0))))
    return [comm, record(0x1A00 + n - 1, "TPDO mapping parameter %d" % n, maps)]


def interop_objects():
    objs = [
        Obj(0x1000, "Device type", var_=var("", U32, "ro", "0x000F0191")),
        Obj(0x1001, "Error register", var_=var("", U8, "ro", "0x00", pdo=True)),
        record(0x1003, "Pre-defined error field",
               [(0, var("Number of errors", U8, "rw", "0"))] +
               [(i, var("Standard error field %d" % i, U32, "ro", "0x00000000")) for i in range(1, 9)],
               otype="0x8"),
        Obj(0x1005, "COB-ID SYNC message", var_=var("", U32, "rw", "0x00000080")),
        Obj(0x1006, "Communication cycle period", var_=var("", U32, "rw", "0")),
        Obj(0x1008, "Manufacturer device name", var_=var("", VSTR, "const", "otfabric canopen-interop device")),
        Obj(0x1014, "COB-ID EMCY", var_=var("", U32, "rw", "$NODEID+0x80")),
        Obj(0x1015, "Inhibit time EMCY", var_=var("", U16, "rw", "0")),
        Obj(0x1017, "Producer heartbeat time", var_=var("", U16, "rw", "0")),
        record(0x1018, "Identity object", [
            (0, highest(4)),
            (1, var("Vendor-ID", U32, "ro", "0x00000000")),
            (2, var("Product code", U32, "ro", "0x434F4931")),
            (3, var("Revision number", U32, "ro", "0x00010000")),
            (4, var("Serial number", U32, "ro", "0x00000001")),
        ]),
        record(0x1200, "SDO server parameter", [
            (0, highest(2)),
            (1, var("COB-ID client to server (rx)", U32, "ro", "$NODEID+0x600")),
            (2, var("COB-ID server to client (tx)", U32, "ro", "$NODEID+0x580")),
        ]),
    ]
    pdos = []
    pdos += rpdo(1, True, [0x20010010, 0x20030020])
    for n in (2, 3, 4):
        pdos += rpdo(n, False, [])
    pdos += tpdo(1, True, [0x20020020])
    for n in (2, 3, 4):
        pdos += tpdo(n, False, [])
    objs += sorted(pdos, key=lambda o: o.index)
    objs += [
        Obj(0x2000, "Test UNSIGNED8", var_=var("", U8, "rw", "0x11", pdo=True)),
        Obj(0x2001, "Test UNSIGNED16", var_=var("", U16, "rw", "0x2233", pdo=True)),
        Obj(0x2002, "Test counter UNSIGNED32", var_=var("", U32, "rw", "0", pdo=True)),
        Obj(0x2003, "Test INTEGER32", var_=var("", I32, "rw", "-12345", pdo=True)),
        Obj(0x2004, "Test VISIBLE_STRING", var_=var("", VSTR, "rw", VISIBLE_STRING_DEFAULT)),
        Obj(0x2005, "Test DOMAIN", var_=var("", DOMAIN, "rw")),
        Obj(0x2006, "Test REAL32", var_=var("", REAL32, "rw", "0", pdo=True)),  # 0: see docs/FIXTURES.md (REAL defaults)
        Obj(0x2007, "Test UNSIGNED64", var_=var("", U64, "rw", "0x0102030405060708", pdo=True)),
        Obj(0x2008, "Test read-only UNSIGNED32", var_=var("", U32, "ro", "0x12345678", pdo=True)),
        Obj(0x2009, "Test write-only UNSIGNED32", var_=var("", U32, "wo", "0")),
        Obj(0x200A, "Test limited UNSIGNED8", var_=var("", U8, "rw", "100", low="10", high="200")),
    ]
    return objs


MANDATORY = (0x1000, 0x1001, 0x1018)


def eds_text(objs, file_name, description, device=None, extra_sections=None, dcf=None):
    device = device or {}
    lines = [
        "; SPDX-License-Identifier: MIT",
        "; Generated by scripts/gen-eds-fixtures.py (canopen-interop); do not edit.",
        "",
        "[FileInfo]",
        "FileName=%s" % file_name,
        "FileVersion=1",
        "FileRevision=0",
        "EDSVersion=4.0",
        "Description=%s" % description,
        "CreationTime=12:00PM",
        "CreationDate=10-29-2026",
        "CreatedBy=otfabric canopen-interop",
        "ModificationTime=12:00PM",
        "ModificationDate=10-29-2026",
        "ModifiedBy=otfabric canopen-interop",
        "",
        "[DeviceInfo]",
        "VendorName=otfabric (test fixture)",
        "VendorNumber=0x00000000",
        "ProductName=%s" % device.get("ProductName", "canopen-interop device"),
        "ProductNumber=0x434F4931",
        "RevisionNumber=0x00010000",
        "OrderCode=canopen-interop",
        "BaudRate_10=1",
        "BaudRate_20=1",
        "BaudRate_50=1",
        "BaudRate_125=1",
        "BaudRate_250=1",
        "BaudRate_500=1",
        "BaudRate_800=1",
        "BaudRate_1000=1",
        "SimpleBootUpMaster=0",
        "SimpleBootUpSlave=1",
        "Granularity=8",
        "DynamicChannelsSupported=0",
        "CompactPDO=0",
        "GroupMessaging=0",
        "NrOfRXPDO=%d" % device.get("NrOfRXPDO", 4),
        "NrOfTXPDO=%d" % device.get("NrOfTXPDO", 4),
        "LSS_Supported=0",
        "",
        "[DummyUsage]",
    ] + ["Dummy%04X=0" % i for i in range(1, 8)] + [""]
    if dcf:
        lines += ["[DeviceComissioning]"] + ["%s=%s" % kv for kv in dcf.items()] + [""]
    mandatory = [o for o in objs if o.index in MANDATORY]
    optional = [o for o in objs if o.index not in MANDATORY and o.index < 0x2000]
    manufacturer = [o for o in objs if 0x2000 <= o.index < 0x6000]
    for title, group in (("MandatoryObjects", mandatory), ("OptionalObjects", optional),
                         ("ManufacturerObjects", manufacturer)):
        lines.append("[%s]" % title)
        lines.append("SupportedObjects=%d" % len(group))
        for i, o in enumerate(group, 1):
            lines.append("%d=0x%04X" % (i, o.index))
        lines.append("")
    for o in objs:
        lines += o.lines()
    for section, items in (extra_sections or []):
        lines.append("[%s]" % section)
        lines += ["%s=%s" % kv for kv in items]
        lines.append("")
    return "\r\n".join(lines).rstrip("\r\n") + "\r\n"


def minimal_objects():
    return [o for o in interop_objects() if o.index in (0x1000, 0x1001, 0x1017, 0x1018, 0x1200)]


def compact_objects():
    """CiA 306 CompactSubObj: an array described by one section."""
    objs = minimal_objects()
    objs.append(Obj(0x2100, "Compact UNSIGNED16 array", otype="0x8", extra={
        "CompactSubObj": "4", "DataType": U16, "AccessType": "rw", "DefaultValue": "0x1234", "PDOMapping": "0"}))
    objs.append(Obj(0x2101, "Compact INTEGER8 array with names", otype="0x8", extra={
        "CompactSubObj": "2", "DataType": "0x0002", "AccessType": "ro", "DefaultValue": "-1", "PDOMapping": "0"}))
    objs.sort(key=lambda o: o.index)
    return objs, [("2101Name", [("NrOfEntries", "2"), ("1", "Compact first"), ("2", "Compact second")])]


def links_objects():
    """CiA 306 ObjectLinks: 0x2200 linked to 0x2201 and 0x2202."""
    objs = minimal_objects() + [
        Obj(0x2200, "Linked parent", var_=var("", U32, "rw", "0")),
        Obj(0x2201, "Linked child one", var_=var("", U16, "rw", "1")),
        Obj(0x2202, "Linked child two", var_=var("", U16, "rw", "2")),
    ]
    objs.sort(key=lambda o: o.index)
    return objs, [("2200ObjectLinks", [("ObjectLinks", "2"), ("1", "0x2201"), ("2", "0x2202")])]


def dcf_objects():
    """A DCF of the interop device for node 5: ParameterValue next to DefaultValue."""
    objs = interop_objects()
    for o in objs:
        if o.index == 0x1017:
            o.var = dict(o.var, ParameterValue="1000")
        if o.index == 0x2001:
            o.var = dict(o.var, ParameterValue="0x4455")
        if o.index == 0x2004:
            o.var = dict(o.var, ParameterValue="configured by DCF")
    return objs


FIXTURES = {
    "interop-device.eds": lambda: eds_text(interop_objects(), "interop-device.eds",
                                           "canopen-interop shared test device"),
    "compact-subobj.eds": lambda: eds_text(compact_objects()[0], "compact-subobj.eds",
                                           "CompactSubObj arrays (CiA 306 4.5.2.4)",
                                           extra_sections=compact_objects()[1]),
    "object-links.eds": lambda: eds_text(links_objects()[0], "object-links.eds",
                                         "ObjectLinks (CiA 306 4.6.2)",
                                         extra_sections=links_objects()[1]),
    "interop-device-node5.dcf": lambda: eds_text(dcf_objects(), "interop-device-node5.dcf",
                                                 "The shared test device configured as node 5 (DCF)",
                                                 dcf={"NodeID": "5", "NodeName": "interop-device", "Baudrate": "500",
                                                      "NetNumber": "1", "NetworkName": "canopen-interop",
                                                      "CANopenManager": "0", "LSS_SerialNumber": "1"}),
    "invalid-missing-datatype.eds": lambda: eds_text(minimal_objects() + [Obj(0x2300, "Missing data type", var_={
        "ParameterName": "", "ObjectType": "0x7", "AccessType": "rw", "DefaultValue": "0", "PDOMapping": "0"})],
        "invalid-missing-datatype.eds", "Invalid: 0x2300 has no DataType"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="fail when a committed fixture differs")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    bad = 0
    for name, make in sorted(FIXTURES.items()):
        text = make().encode()
        path = os.path.join(OUT, name)
        if args.check:
            if not os.path.exists(path) or open(path, "rb").read() != text:
                print("fixtures/eds/%s differs from scripts/gen-eds-fixtures.py" % name)
                bad = 1
        else:
            with open(path, "wb") as f:
                f.write(text)
    if args.check and not bad:
        print("%d EDS/DCF fixtures match scripts/gen-eds-fixtures.py" % len(FIXTURES))
    sys.exit(bad)


if __name__ == "__main__":
    main()
