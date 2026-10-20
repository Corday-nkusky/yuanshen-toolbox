import glob
import os

import ms_toollib as ms

root = r"C:\Users\TANGWENJIAN\AppData\Roaming\Metasweeper3.3.4\replay"
f = sorted(glob.glob(os.path.join(root, "*.evf")))[0]
print("file", os.path.basename(f), flush=True)
v = ms.EvfVideo(f)
v.parse()
print("raw_data len", len(v.raw_data), flush=True)
print("events type", type(v.events), "len", len(v.events), flush=True)
for i, ev in enumerate(v.events[:12]):
    print(i, ev, flush=True)
print("...", flush=True)
print("board row/col", v.row, v.column, "mine", v.mine_num, "pix", v.pix_size, flush=True)
print("rtime", v.rtime, "rtime_ms", v.rtime_ms, "mode", v.mode, flush=True)
b = v.board
print("board len", len(b), len(b[0]) if b else 0, flush=True)
for line in b[:4]:
    print("  ", line, flush=True)
