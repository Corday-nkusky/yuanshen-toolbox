"""GUI 冒烟测试：起窗口、等扫描、校验内容、截图。"""
import os
import time
import app as A
from PIL import ImageGrab

a = A.App(r" C:\Users\TANGWENJIAN\AppData\Roaming\Metasweeper3.3.4\replay".strip())
a.attributes("-topmost", True)

deadline = time.time() + 8
while time.time() < deadline:
    a.update()
    if not a._scanning and a.games:
        break
    time.sleep(0.03)
for _ in range(10):
    a.update()
    time.sleep(0.03)

print("games      =", len(a.games))
print("pb_rows    =", len(a.pb_rows))
print("summary_b  =", a.summary["b"].get("games"), a.summary["b"].get("bvCount"))
print("status     =", a.lbl_status.cget("text"))
print("check      =", a.lbl_check.cget("text"))


def shot(name):
    a.update()
    time.sleep(0.25)
    a.update()
    x, y = a.winfo_rootx(), a.winfo_rooty()
    w, h = a.winfo_width(), a.winfo_height()
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
    img.save(os.path.join(os.path.dirname(os.path.abspath(__file__)), name))
    print("shot", name, img.size)


shot("shot_overview.png")

a.show_tab("pb")
a.render_pb()
shot("shot_pb.png")
print("pb table rows =", len(a.tbl_pb.rows))
print("bvmin          =", a.var_bvmin.get(), "| level", a._level)

a.show_tab("top")
a.set_top_level("b")
shot("shot_top.png")
for m, t in a.top_tables.items():
    print(f"top[{m}] rows =", len(t.rows))

# 交互：排序 + 筛选 + 搜索
a.tbl_pb._sort_by("time")
a.set_pb_level("b")
q = len(a.tbl_pb.rows)
a.var_q.set("1.774")
print("search rows =", len(a.tbl_pb.rows), "(was", q, ")")
a.var_q.set("")
a.set_pb_level("")

a.destroy()
print("DONE")
