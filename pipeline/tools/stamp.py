"""Stamp a new build id into index.html and version.json, so open copies of the game reload themselves.
Run before every commit that changes index.html: python3 pipeline/tools/stamp.py"""
import json, re, time, pathlib
root = pathlib.Path(__file__).resolve().parents[2]
build = time.strftime('%Y%m%d%H%M%S', time.gmtime())
p = root / 'index.html'; s = p.read_text()
s, n = re.subn(r"const BUILD = '[^']*';", f"const BUILD = '{build}';", s); assert n == 1
p.write_text(s); (root / 'version.json').write_text(json.dumps({'build': build}) + '\n')
print(build)
