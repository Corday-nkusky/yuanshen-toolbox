import glob
import os

import ms_toollib as ms

root = r"C:\Users\TANGWENJIAN\AppData\Roaming\Metasweeper3.3.4\replay"
fs = sorted(glob.glob(os.path.join(root, "*.evf")))
print("%-26s %-4s %-3s %-3s %-3s %-8s %-5s %-5s %-5s %s" % (
    "name", "hdr", "rce", "rc", "lft", "rtime", "bbbv", "solv", "flag", "done"), flush=True)
bad = 0
for f in fs:
    n = os.path.basename(f)
    v = ms.EvfVideo(f)
    v.parse()
    v.analyse()
    kp = v.events[-1].key_dynamic_params
    hdr_nf = bool(v.raw_data[0] & 0x10)
    calc_nf = (kp.rce == 0)
    flag_txt = "" if hdr_nf == calc_nf else "  <-- 不一致"
    if hdr_nf != calc_nf:
        bad += 1
    print("%-26s %-4s %-3s %-3s %-3s %-8.3f %-5s %-5s %-5s %s%s" % (
        n[:26], int(hdr_nf), kp.rce, kp.right, kp.left, v.rtime, v.bbbv,
        kp.bbbv_solved, kp.flag, v.is_completed, flag_txt), flush=True)
print("mismatch:", bad, "/", len(fs), flush=True)
