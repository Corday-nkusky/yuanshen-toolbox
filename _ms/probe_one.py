import glob
import os
import sys
import traceback

import ms_toollib as ms

root = r"C:\Users\TANGWENJIAN\AppData\Roaming\Metasweeper3.3.4\replay"
fs = sorted(glob.glob(os.path.join(root, "*.evf")))
print("files", len(fs), flush=True)
f = fs[0]
print("file", os.path.basename(f), flush=True)
v = ms.EvfVideo(f)
print("constructed; rtime =", v.rtime, "bbbv =", v.bbbv, "pix =", v.pix_size, flush=True)
print("calling analyse()", flush=True)
try:
    v.analyse()
    print("analysed; rce =", v.rce, "nf =", v.nf, "flag =", v.flag, flush=True)
except BaseException:
    traceback.print_exc()
    sys.stderr.flush()
