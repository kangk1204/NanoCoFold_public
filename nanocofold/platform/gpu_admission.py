"""Read the selected NVIDIA GPU without allocating device memory."""
import re
import subprocess
from xml.etree import ElementTree


def _snapshot(gpu):
    output = subprocess.run(["nvidia-smi", "-i", gpu, "-q", "-x"], capture_output=True,
                            text=True, timeout=2, check=True).stdout
    devices = ElementTree.fromstring(output).findall("gpu")
    if len(devices) != 1:
        raise ValueError("Expected one selected GPU")
    return devices[0]


def _number(device, field, unit):
    match = re.fullmatch(r"(\d+)\s*" + unit, device.findtext(field, "").strip())
    if not match:
        raise ValueError("Missing GPU measurement")
    return int(match[1])


def availability(gpu):
    try:
        device = _snapshot(gpu)
        total = _number(device, "fb_memory_usage/total", "MiB")
        free = _number(device, "fb_memory_usage/free", "MiB")
        utilization = _number(device, "utilization/gpu_util", "%")
        processes = device.find("processes")
        if (not 0 < total or not 0 <= free <= total or not 0 <= utilization <= 100
                or processes is None or (processes.text or "").strip()):
            raise ValueError("Incomplete GPU telemetry")
        types = [p.findtext("type","").strip() for p in processes.findall("process_info")]
        if any(kind not in {"G", "C+G", "C", "M", "M+C"} for kind in types):
            raise ValueError("Unknown GPU process type")
    except (OSError, ValueError, ElementTree.ParseError, subprocess.SubprocessError):
        return {"reason": "telemetry"}
    # Desktop applications can keep C+G contexts all day. Their memory and
    # utilization still count. Pure compute and MPS contexts reserve the device.
    reason = ("process" if any(kind not in {"G","C+G"} for kind in types)
              else "memory" if free * 100 < total * 80
              else "utilization" if utilization > 20 else None)
    return {"reason": reason, "total_mib": total, "free_mib": free, "utilization": utilization}
