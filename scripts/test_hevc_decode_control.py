#!/usr/bin/env python3
"""Reject incomplete/contradictory standalone decoder receipts."""
import copy, importlib.util, unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('hevc',Path(__file__).with_name('run-hevc-decode-control.py'))
hevc=importlib.util.module_from_spec(spec);spec.loader.exec_module(hevc)
class ReceiptTests(unittest.TestCase):
    def setUp(self):
        frame={'frame_md5':['0'*32], 'frames':1,'width':320,'height':240,'bit_depth':8}
        self.result={'diagnostic_only':True,'corpus_admission':False,'software_only':True,
                     'decoder':'hevc','passes':[frame,copy.deepcopy(frame)],'seek_and_flush_completed':True}
        self.expected={'md5_checksums':['0'*32],'num_frames':1,'width':320,'height':240}
    def test_complete(self):
        self.assertTrue(hevc.validate_decode_result(self.result,8,self.expected)['golden_md5_matched'])
    def test_empty_cannot_pass(self):
        for p in self.result['passes']:p.update(frames=0,frame_md5=[])
        with self.assertRaises(AssertionError):hevc.validate_decode_result(self.result,8)
    def test_different_reset_cannot_pass(self):
        self.result['passes'][1]['frame_md5']=['1'*32]
        with self.assertRaises(AssertionError):hevc.validate_decode_result(self.result,8)
    def test_wrong_golden_cannot_pass(self):
        self.expected['md5_checksums']=['1'*32]
        with self.assertRaises(AssertionError):hevc.validate_decode_result(self.result,8,self.expected)
    def test_wrong_bit_depth_cannot_pass(self):
        with self.assertRaises(AssertionError):hevc.validate_decode_result(self.result,10,self.expected)
    def test_incomplete_count_cannot_pass(self):
        for p in self.result['passes']:p['frames']=2
        with self.assertRaises(AssertionError):hevc.validate_decode_result(self.result,8)
    def test_hardware_or_admission_claim_cannot_pass(self):
        self.result['software_only']=False
        with self.assertRaises(AssertionError):hevc.validate_decode_result(self.result,8)
        self.result['software_only']=True;self.result['corpus_admission']=True
        with self.assertRaises(AssertionError):hevc.validate_decode_result(self.result,8)
if __name__=='__main__':unittest.main()
