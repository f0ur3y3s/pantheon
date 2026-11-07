"""Stand-in for LM Studio's `lms` CLI, replaying output formats recorded on the MBP (2026-11-06).

State lives in the JSON file named by FAKE_LMS_STATE. Models whose key contains "huge" fail the guardrail.
"""

import json
import os
import sys
import time

STATE = os.environ["FAKE_LMS_STATE"]
CATALOG = [
    {"type": "llm", "modelKey": "qwen/qwen3.6-35b-a3b", "displayName": "Qwen3.6 35B A3B", "sizeBytes": 20429364306},
    {"type": "llm", "modelKey": "prism-ml/bonsai-27b", "displayName": "Bonsai 27B", "sizeBytes": 8521076756},
    {"type": "llm", "modelKey": "huge/model", "displayName": "Huge", "sizeBytes": 99 * 2**30},
    {"type": "embedding", "modelKey": "text-embedding-nomic-embed-text-v1.5", "displayName": "Nomic", "sizeBytes": 84106624},
]


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"server": True, "loaded": []}


def save(s):
    with open(STATE, "w") as f:
        json.dump(s, f)


def opt(args, name, default=None):
    return args[args.index(name) + 1] if name in args else default


def main(argv):
    s = load_state()
    cmd, args = argv[0], argv[1:]
    if cmd == "version":
        print(json.dumps({"version": "69d945a"}))
    elif cmd == "ls":
        print(json.dumps(CATALOG))
    elif cmd == "ps":
        print(json.dumps(s["loaded"]))
    elif cmd == "server":
        if args[0] == "status":
            print(json.dumps({"running": s["server"], "port": 8080}))
        elif args[0] == "start":
            s["server"] = True
            save(s)
            print("Success! Server is now running on port 8080")
    elif cmd == "load":
        key = args[0]
        ident = opt(args, "--identifier", key)
        if "huge" in key:
            print("\x1b[?25l\rLoading huge/model 0% ", end="", flush=True)
            print("\nError: Model loading was stopped due to insufficient system resources. Continuing to load the "
                  "model would likely overload your system and cause it to freeze.")
            return 1
        for pct in (0, 0, 12, 37, 52, 100):
            sys.stdout.write(f"\x1b[?25l\rLoading {key} {pct}% ⠋")
            sys.stdout.flush()
            time.sleep(0.02)
        sys.stdout.write("\r\x1b[K\x1b[?25hModel loaded successfully in 0.12s.\n(7.94 GiB)\n")
        print(f'To use the model in the API/SDK, use the identifier "{ident}".')
        entry = next(m for m in CATALOG if m["modelKey"] == key)
        s["loaded"].append({**entry, "identifier": ident, "contextLength": int(opt(args, "-c", 4096)),
                            "parallel": int(opt(args, "--parallel", 4)), "status": "idle", "args": args})
        save(s)
    elif cmd == "unload":
        before = len(s["loaded"])
        s["loaded"] = [m for m in s["loaded"] if m["identifier"] != args[0]]
        save(s)
        if len(s["loaded"]) == before:
            print(f'Error: no model with identifier "{args[0]}"')
            return 1
        print(f'Model "{args[0]}" unloaded.')
    else:
        print(f"Error: unknown command {cmd}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
