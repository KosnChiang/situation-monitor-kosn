"""GPU sanity check.

Run:
    python -m tools.gpu_check

Prints torch.cuda availability, device count, per-device name, and the
effective CUDA_VISIBLE_DEVICES the process sees. Exits non-zero if torch
is missing or no CUDA device is visible.
"""
from __future__ import annotations

import json
import os
import sys


def collect() -> dict:
    info: dict = {
        "cuda_visible_devices": os.getenv("CUDA_VISIBLE_DEVICES", ""),
        "torch_installed": False,
        "torch_version": None,
        "cuda_available": False,
        "device_count": 0,
        "devices": [],
    }
    try:
        import torch  # type: ignore
    except Exception as e:
        info["error"] = f"torch import failed: {e!r}"
        return info

    info["torch_installed"] = True
    info["torch_version"] = torch.__version__
    info["cuda_available"] = bool(torch.cuda.is_available())
    if not info["cuda_available"]:
        return info

    info["device_count"] = int(torch.cuda.device_count())
    for i in range(info["device_count"]):
        try:
            props = torch.cuda.get_device_properties(i)
            info["devices"].append(
                {
                    "index": i,
                    "name": torch.cuda.get_device_name(i),
                    "total_memory_gb": round(props.total_memory / (1024**3), 2),
                    "capability": f"{props.major}.{props.minor}",
                }
            )
        except Exception as e:  # pragma: no cover - hardware-dependent
            info["devices"].append({"index": i, "error": repr(e)})
    return info


def main() -> int:
    info = collect()
    print(json.dumps(info, indent=2, ensure_ascii=False))
    if not info["torch_installed"]:
        print("FAIL: torch not installed. Run scripts\\setup_windows.ps1", file=sys.stderr)
        return 2
    if not info["cuda_available"]:
        print("FAIL: no CUDA device visible to this process.", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
