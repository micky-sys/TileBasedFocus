import csv
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("decode",ROOT/"tools/decode.py")
decode=importlib.util.module_from_spec(spec)
spec.loader.exec_module(decode)


class DecoderTests(unittest.TestCase):
    fixture=ROOT/"build/test-fixture.XPL"

    def test_cpp_fixture_and_scaling(self):
        report,anchors=decode.scan([self.fixture])
        self.assertTrue(report["integrity_ok"],report)
        for sensor in report["sensors"]:
            self.assertEqual(sensor["samples"],480)
            self.assertEqual(sensor["anchor_count"],3)
            self.assertAlmostEqual(sensor["measured_rate_hz"],16000)
            self.assertEqual(sensor["timestamp_jumps"],0)
        self.assertEqual(anchors[0][0],(0,10032))
        with tempfile.TemporaryDirectory() as d:
            decode.export_csv([self.fixture],Path(d),anchors)
            with open(Path(d)/"sensor_1.csv") as f:
                rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),480)
            self.assertEqual(float(rows[0]["az_g"]),1)
            self.assertEqual(float(rows[0]["gx_dps"]),250)
            self.assertEqual(float(rows[80]["mcu_us"]),15032)
            self.assertEqual(rows[80]["time_quality"],"interpolated")
            self.assertEqual(rows[-1]["time_quality"],"nominal_extrapolation")

    def scan_bytes(self,data):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"test.XPL";p.write_bytes(data)
            return decode.scan([p])[0]

    def test_detect_corruption(self):
        data=bytearray(self.fixture.read_bytes());data[150]^=1
        report=self.scan_bytes(data)
        self.assertFalse(report["integrity_ok"])
        self.assertTrue(any("CRC" in e for e in report["errors"]))

    def test_detect_partial_write_and_missing_footer(self):
        for removed in [1,20,100,300]:
            report=self.scan_bytes(self.fixture.read_bytes()[:-removed])
            self.assertFalse(report["integrity_ok"])
            self.assertFalse(report["clean_end"])

    def test_detect_missing_frame_even_with_valid_crc(self):
        data=self.fixture.read_bytes()
        first_size=24+struct.unpack_from("<H",data,4)[0]
        second_size=24+struct.unpack_from("<H",data,first_size+4)[0]
        report=self.scan_bytes(data[:first_size]+data[first_size+second_size:])
        self.assertTrue(any("sequence" in e for e in report["errors"]))

    def test_reject_bad_packet_length(self):
        with self.assertRaises(ValueError):
            list(decode.packets(struct.pack("<II",0,10)+bytes(16)))

    def rewritten(self, change):
        result=bytearray()
        for seq,(kind,source,time,data) in enumerate(decode.frames([self.fixture])):
            data=change(kind,source,time,data)
            header=struct.pack("<IHBBIQ",decode.MAGIC,len(data),kind,source,seq,time)
            result.extend(header+struct.pack("<I",zlib.crc32(data,zlib.crc32(header)))+data)
        return result

    def test_sample_gap_despite_valid_frame_checksums(self):
        def change(kind,source,time,data):
            if kind==2 and source==0:
                index=struct.unpack_from("<I",data)[0]
                if index>=160:
                    return struct.pack("<I",index+16)+data[4:]
            return data
        report=self.scan_bytes(self.rewritten(change))
        self.assertFalse(report["integrity_ok"])
        self.assertEqual(report["sensors"][0]["excluded_or_missing"],16)
        self.assertTrue(any("missing continuous" in e for e in report["errors"]))

    def test_invalid_data_and_range_are_flagged(self):
        def change(kind,source,time,data):
            if kind==2 and source==0:
                p=bytearray(data);p[9:11]=struct.pack(">h",-32768);p[15:17]=struct.pack(">h",32767)
                return bytes(p)
            return data
        report=self.scan_bytes(self.rewritten(change))
        self.assertGreater(report["sensors"][0]["invalid"],0)
        self.assertGreater(report["sensors"][0]["saturated"],0)

    def test_no_invented_synchronization(self):
        self.assertEqual(decode.sample_time(100,[],[]),("","unsynchronized"))
        t,q=decode.sample_time(1,[(0,1000)],[0])
        self.assertEqual((t,q),(1062.5,"nominal_extrapolation"))


if __name__=="__main__":
    unittest.main()
