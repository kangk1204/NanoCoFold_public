"""Read the selected NVIDIA GPU without allocating device memory."""

import contextlib
import re
import subprocess
from xml.etree import ElementTree


def _snapshot(gpu):
    output = subprocess.run(
        ["nvidia-smi", "-i", gpu, "-q", "-x"], capture_output=True, text=True, timeout=2, check=True
    ).stdout
    devices = ElementTree.fromstring(output).findall("gpu")
    if len(devices) != 1:
        raise ValueError("Expected one selected GPU")
    return devices[0]


def _number(device, field, unit):
    match = re.fullmatch(r"(\d+)\s*" + unit, device.findtext(field, "").strip())
    if not match:
        raise ValueError("Missing GPU measurement")
    return int(match[1])


def is_shared_analysis(job):
    return job["kind"] == "analysis" and not job.get("mock")


def shared_snapshot(gpu):
    """Read the selected device. Numeric and UUID requests must use the same reservation namespace."""
    device = _snapshot(gpu)
    uuid = device.findtext("uuid", "").strip()
    if not re.fullmatch(r"GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", uuid):
        raise ValueError("Unknown GPU identity")
    uuid = "GPU-" + uuid[4:].lower()
    if gpu.isdigit():
        index = str(int(gpu))
    else:
        if gpu.lower() != uuid.lower():
            raise ValueError("GPU identity changed")
        index = subprocess.run(
            ["nvidia-smi", "-i", uuid, "--query-gpu=index", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        ).stdout.strip()
        if not index.isdecimal():
            raise ValueError("Unknown GPU index")
    total = _number(device, "fb_memory_usage/total", "MiB")
    free = _number(device, "fb_memory_usage/free", "MiB")
    utilization = _number(device, "utilization/gpu_util", "%")
    if not (total > 0 and 0 <= free <= total and 0 <= utilization <= 100):
        raise ValueError("Invalid GPU telemetry")
    processes = device.find("processes")
    if processes is None or (processes.text or "").strip():
        raise ValueError("No GPU process telemetry")
    # Unknown PID/memory entries get no credit against a reservation. Their memory
    # is already subtracted from free_mib by the driver.
    allocated = {}
    for process in processes.findall("process_info"):
        pid = process.findtext("pid", "").strip()
        amount = re.fullmatch(r"(\d+)\s*MiB", process.findtext("used_memory", "").strip())
        if pid.isdecimal() and amount:
            allocated[int(pid)] = max(allocated.get(int(pid), 0), int(amount[1]))
    return uuid, index, total, free, utilization, allocated


def reservation_budget(total, free, reservations, allocated, requested=None):
    """Count each active reservation at its greater of observed or reserved memory."""
    margin = max(1024, (total + 9) // 10)
    budget = min(6144, total - margin) if requested is None else requested
    if budget <= 0:
        raise ValueError("Invalid GPU memory budget")
    unallocated = sum(max(0, capacity - allocated.get(key, 0)) for key, capacity in reservations.items())
    headroom = max(0, free - unallocated - margin)
    return {
        "reason": "capacity" if headroom < budget else None,
        "job_mib": budget,
        "available_mib": headroom,
        "margin_mib": margin,
        "reservations": len(reservations),
    }


def _process_reservations(processes, reservations):
    import os
    from pathlib import Path

    usage = {}
    if not reservations:
        return usage
    for pid, amount in processes.items():
        # Never trust a numeric process ID alone. Reuse credit only if the process
        # holds a live slot inode. Engines outside this installation get zero credit.
        with contextlib.suppress(OSError):
            with os.scandir(Path("/proc") / str(pid) / "fd") as entries:
                for entry in entries:
                    with contextlib.suppress(OSError):
                        st = entry.stat()
                        key = (st.st_dev, st.st_ino)
                        if key in reservations:
                            usage[key] = usage.get(key, 0) + amount
                            break
    return usage


@contextlib.contextmanager
def shared_reservation(gpu, directory):
    """Short admission transaction. Only lifetime descriptors are handed to the worker."""
    import fcntl
    import os
    import stat

    def open_lock(path):
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        return os.fdopen(fd, "r+b")

    def acquire(file, mode):
        try:
            fcntl.flock(file, mode | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def prepare(transaction, lifetime):
        if not acquire(transaction.enter_context(open_lock(directory / "gpu-admission.lock")), fcntl.LOCK_EX):
            return {"reason": "admission"}, []
        try:
            uuid, index, total, free, utilization, processes = shared_snapshot(gpu)
        except (OSError, ValueError, ElementTree.ParseError, subprocess.SubprocessError):
            return {"reason": "telemetry"}, []
        details = {"uuid": uuid, "total_mib": total, "free_mib": free, "utilization": utilization}
        files = []
        for name in (uuid, index):
            handle = lifetime.enter_context(open_lock(directory / f"gpu-{name}.lock"))
            if not acquire(handle, fcntl.LOCK_SH):
                return {**details, "reason": "exclusive"}, []
            files.append(handle)
        reservations = {}
        slot = None
        for path in sorted(directory.glob(f"gpu-{uuid}.slot-*")):
            with open_lock(path) as file:
                if acquire(file, fcntl.LOCK_EX):
                    if slot is None:
                        slot = path
                else:
                    st = os.fstat(file.fileno())
                    contents = file.read(32)
                    if not stat.S_ISREG(st.st_mode) or not contents.isdigit():
                        return {**details, "reason": "telemetry"}, []
                    reservations[st.st_dev, st.st_ino] = int(contents)
        requested = os.environ.get("NANOCOFOLD_GPU_JOB_MIB")
        if requested is not None and (
            len(requested) > 10 or not requested.isascii() or not requested.isdecimal() or int(requested) <= 0
        ):
            return {**details, "reason": "policy"}, []
        budget = reservation_budget(
            total,
            free,
            reservations,
            _process_reservations(processes, reservations),
            int(requested) if requested else None,
        )
        details.update(budget)
        if not details["reason"] and utilization >= 90:
            details["reason"] = "utilization"
        if details["reason"]:
            return details, []
        slot = slot or directory / f"gpu-{uuid}.slot-{len(reservations)}"
        handle = lifetime.enter_context(open_lock(slot))
        if not acquire(handle, fcntl.LOCK_EX):
            return {**details, "reason": "admission"}, []
        handle.seek(0)
        handle.truncate()
        handle.write(str(budget["job_mib"]).encode("ascii"))
        handle.flush()
        return details, [*files, handle]

    with contextlib.ExitStack() as lifetime:
        with contextlib.ExitStack() as transaction:
            reading, files = prepare(transaction, lifetime)
        yield reading, files
