"""Structured output: human text or JSON, consistently."""
from __future__ import annotations
import json
import sys


def emit(obj: dict, *, as_json: bool) -> None:
    if as_json:
        json.dump(obj, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        if obj.get("ok") is False:
            print(f"error[{obj.get('error')}]: {obj.get('message')}", file=sys.stderr)
        else:
            for k, v in obj.items():
                if k == "ok":
                    continue
                print(f"{k}: {v}")
    sys.stdout.flush()
