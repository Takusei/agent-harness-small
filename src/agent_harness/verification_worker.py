"""Trusted invocation wrapper, sent to Docker only; never execute candidate code on host."""

import contextlib
import importlib.util
import json
import sys


def main():
    request = json.loads(sys.argv[1])
    # Expected answers never enter this process. Capture incidental prints on stderr.
    try:
        with contextlib.redirect_stdout(sys.stderr):
            spec = importlib.util.spec_from_file_location(
                "candidate", "/workspace/" + request["module"] + ".py"
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            value = getattr(module, request["function"])(*request["args"])
        payload = {"value": value}
        encoded = json.dumps(payload, allow_nan=False)
    except Exception as exc:
        encoded = json.dumps({"error": f"{type(exc).__name__}: {str(exc)[:2000]}"})
    print(encoded)


if __name__ == "__main__":
    main()
