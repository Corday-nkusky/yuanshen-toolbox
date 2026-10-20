"""抓取 saolei_website 仓库文件（走 api.github.com contents 接口，base64 解码）。

raw.githubusercontent.com 在本机 TLS 偶发握手失败，api.github.com 稳定。
"""
import base64
import json
import os
import sys
import time
import urllib.request

REPO = "eee555/saolei_website"
BRANCH = "main"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)))


def get(path, tries=4):
    url = f"https://api.github.com/repos/{REPO}/contents/{path}?ref={BRANCH}"
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "pb-viewer-research",
                "Accept": "application/vnd.github+json",
            })
            with urllib.request.urlopen(req, timeout=45) as r:
                d = json.load(r)
            return base64.b64decode(d["content"])
        except Exception as e:
            print(f"  retry {i+1} {path}: {e}", file=sys.stderr)
            time.sleep(1.5)
    return None


def main(paths):
    for p in paths:
        data = get(p)
        if data is None:
            print(f"FAIL {p}")
            continue
        name = p.replace("/", "_")
        with open(os.path.join(OUT, name), "wb") as f:
            f.write(data)
        print(f"ok {len(data):>7} {p} -> {name}")


if __name__ == "__main__":
    main(sys.argv[1:])
