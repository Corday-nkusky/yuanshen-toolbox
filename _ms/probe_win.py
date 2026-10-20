import os

import app as A

a = A.App(os.path.join(os.environ["APPDATA"], "Metasweeper3.3.4", "replay"))
for _ in range(40):
    a.update()

out = os.path.join("D:/metasweeper-pb", "probe.txt")
with open(out, "w", encoding="utf-8") as fh:
    fh.write("SCALE=%s\n" % A.SCALE)
    fh.write("screen=%sx%s\n" % (a.winfo_screenwidth(), a.winfo_screenheight()))
    fh.write("win=%sx%s\n" % (a.winfo_width(), a.winfo_height()))
    fh.write("geom=%s\n" % a.winfo_geometry())
    fh.write("maxsize=%s\n" % (a.maxsize(),))
    fh.write("req=%sx%s\n" % (a.winfo_reqwidth(), a.winfo_reqheight()))
    for k, p in a.pages.items():
        fh.write("page %s req=%sx%s\n" % (k, p.winfo_reqwidth(), p.winfo_reqheight()))
a.destroy()
print("written")
