#!/usr/bin/env python3
import argparse, json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.shadow_binding import bind_context_shadow
def main(argv=None):
    p=argparse.ArgumentParser(description="Bind context sidecars in report-only shadow mode"); p.add_argument("packet",type=Path); p.add_argument("context",type=Path); p.add_argument("receipt",type=Path); p.add_argument("policy",type=Path); a=p.parse_args(argv)
    values=[json.loads(x.read_text(encoding="utf-8")) for x in (a.packet,a.context,a.receipt,a.policy)]
    result=bind_context_shadow(*values); print(json.dumps(result,indent=2,sort_keys=True)); return 0 if result["contextShadow"]["status"]=="SHADOW_PASS" else 2
if __name__=="__main__": raise SystemExit(main())
