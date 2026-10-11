"""Helpers over the Ghidra dump. usage: funcs.py <dump> callers <regex> | show <name> | cmds <regex-of-callee>"""
import re, sys
sys.stdout.reconfigure(encoding="utf-8")
txt = open(sys.argv[1], encoding="utf-8").read()
parts = re.split(r"\n//==== FUNC (\S+) @ (\S+) size=(\d+)\n", txt)
funcs = {parts[i]: parts[i + 3] for i in range(1, len(parts) - 3, 4)}
mode, arg = sys.argv[2], sys.argv[3]
if mode == "callers":
    for n, b in funcs.items():
        if re.search(arg + r"\(", b) and not b.lstrip().split("(")[0].endswith(arg):
            print(n)
elif mode == "show":
    print(funcs[arg])
elif mode == "cmds":
    for n, b in sorted(funcs.items()):
        if re.search(arg, b):
            print(n, sorted(set(re.findall(r"\[3\] = ([^;]+);", b))))
elif mode == "grep":
    for n, b in funcs.items():
        if re.search(arg, b):
            print(n)
