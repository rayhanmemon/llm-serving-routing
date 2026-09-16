#!/usr/bin/env python3
"""Bounded raw CUDA IPC diagnostic; no PyTorch, NIXL, or performance claims.

Driver ABI: https://docs.nvidia.com/cuda/archive/13.0.3/cuda-driver-api/group__CUDA__MEM.html
Use unique stems. Copy metadata to the consumer and its done file back to producer.
An external timeout (115 seconds) must bound a driver call that hangs in native code.
"""
import argparse
import base64
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import time
import uuid


class Handle(C.Structure):
    _fields_ = [("reserved", C.c_ubyte * 64)]


class UUID(C.Structure):
    _fields_ = [("bytes", C.c_ubyte * 16)]


PTR = C.c_uint64
P = C.POINTER
SIGNATURES = {
    "cuInit": [C.c_uint], "cuDriverGetVersion": [P(C.c_int)],
    "cuDeviceGetCount": [P(C.c_int)], "cuDeviceGet": [P(C.c_int), C.c_int],
    "cuDeviceGetUuid_v2": [P(UUID), C.c_int],
    "cuDevicePrimaryCtxRetain": [P(C.c_void_p), C.c_int],
    "cuDevicePrimaryCtxRelease_v2": [C.c_int], "cuCtxSetCurrent": [C.c_void_p],
    "cuCtxSynchronize": [], "cuMemAlloc_v2": [P(PTR), C.c_size_t],
    "cuMemFree_v2": [PTR], "cuMemsetD8_v2": [PTR, C.c_ubyte, C.c_size_t],
    "cuIpcGetMemHandle": [P(Handle), PTR],
    # CUipcMemHandle is passed BY VALUE, not as a pointer.
    "cuIpcOpenMemHandle": [P(PTR), Handle, C.c_uint],
    "cuIpcCloseMemHandle": [PTR],
    "cuMemcpyDtoD_v2": [PTR, PTR, C.c_size_t],
    "cuMemcpyDtoH_v2": [C.c_void_p, PTR, C.c_size_t],
    "cuGetErrorName": [C.c_int, P(C.c_char_p)],
    "cuGetErrorString": [C.c_int, P(C.c_char_p)],
}


class Driver:
    def __init__(self, path, result):
        self.lib = C.CDLL(path)
        self.result = result
        for name, types in SIGNATURES.items():
            fn = getattr(self.lib, name)
            fn.argtypes, fn.restype = types, C.c_int

    def call(self, name, *args):
        self.result["stage"] = name
        code = getattr(self.lib, name)(*args)
        event = {"api": name, "code": code}
        if code:
            for key, api in (("name", "cuGetErrorName"), ("description", "cuGetErrorString")):
                value = C.c_char_p()
                if getattr(self.lib, api)(code, C.byref(value)) == 0 and value.value:
                    event[key] = value.value.decode(errors="replace")
        self.result["calls"].append(event)
        if code:
            raise RuntimeError(json.dumps(event))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n")
    os.replace(tmp, path)


def wait_packet(path, session, deadline):
    while time.monotonic() < deadline:
        try:
            packet = json.loads(Path(path).read_text())
        except FileNotFoundError:
            time.sleep(0.1)
            continue
        if packet.get("session_id") != session:
            raise ValueError("packet session_id mismatch")
        return packet
    raise TimeoutError(f"waiting for {path}")


def decode_handle(packet, session, size):
    if packet.get("session_id") != session or packet.get("bytes") != size:
        raise ValueError("metadata session_id or allocation size mismatch")
    if packet.get("fill_byte") != 73:
        raise ValueError("metadata fill_byte mismatch")
    raw = base64.b64decode(packet["handle_base64"], validate=True)
    if len(raw) != C.sizeof(Handle) or hashlib.sha256(raw).hexdigest() != packet["handle_sha256"]:
        raise ValueError("IPC handle length or SHA256 mismatch")
    return Handle.from_buffer_copy(raw)


def read_text(path):
    try:
        return Path(path).read_text().strip()
    except OSError as exc:
        return {"unavailable": str(exc)}


def environment():
    namespaces = {}
    for name in ("ipc", "pid", "user", "mnt", "net"):
        try:
            namespaces[name] = os.readlink(f"/proc/self/ns/{name}")
        except OSError as exc:
            namespaces[name] = {"unavailable": str(exc)}
    devices = []
    for path in sorted(Path("/dev").glob("nvidia*")):
        info = path.stat()
        devices.append({"path": str(path), "mode": stat.filemode(info.st_mode),
                        "uid": info.st_uid, "gid": info.st_gid,
                        "major": os.major(info.st_rdev), "minor": os.minor(info.st_rdev)})
    return {"pid": os.getpid(), "uid": os.getuid(), "euid": os.geteuid(),
            "gid": os.getgid(), "groups": os.getgroups(), "namespaces": namespaces,
            "device_nodes": devices, "kernel_nvidia_version": read_text("/proc/driver/nvidia/version"),
            "environment": {key: os.environ.get(key) for key in
                            ("CUDA_VISIBLE_DEVICES", "NVIDIA_VISIBLE_DEVICES", "LD_LIBRARY_PATH")}}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("producer", "consumer", "inspect"))
    parser.add_argument("--device", type=int, required=True)
    parser.add_argument("--stem", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--bytes", type=int, default=2097152)
    parser.add_argument("--libcuda", default="libcuda.so.1")
    args = parser.parse_args(argv)
    if not (0 < args.timeout <= 90) or not (0 < args.bytes <= 2097152) or args.device < 0 or not args.session_id:
        parser.error("timeout must be in (0,90], bytes in [1,2097152], device >=0, session-id nonempty")
    return args


def run(args):
    started = time.monotonic()
    deadline = started + args.timeout
    result = {"role": args.role, "session_id": args.session_id, "device": args.device,
              "bytes": args.bytes, "ok": False, "calls": [], "errors": [], "cleanup": [],
              "libcuda_requested": args.libcuda, "stage": "environment"}
    driver = None
    context, own, imported, device = C.c_void_p(), PTR(), PTR(), C.c_int()
    retained = exported = safe_export_free = False
    import_active = False
    metadata = args.stem + ".metadata.json"
    done = args.stem + ".done.json"

    def timeout(signum, frame):
        raise TimeoutError("probe deadline exceeded")

    def cleanup(name, *values):
        try:
            driver.call(name, *values)
            result["cleanup"].append({"api": name, "ok": True})
            return True
        except Exception as exc:
            result["errors"].append({"stage": name, "cleanup": True, "error": str(exc)})
            return False

    old_handler = signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, args.timeout)
    try:
        result.update(environment())
        result["stage"] = "load_libcuda"
        driver = Driver(args.libcuda, result)
        maps = read_text("/proc/self/maps")
        result["loaded_libcuda_maps"] = [line for line in maps.splitlines() if "libcuda" in line] if isinstance(maps, str) else maps
        driver.call("cuInit", 0)
        version, count = C.c_int(), C.c_int()
        driver.call("cuDriverGetVersion", C.byref(version))
        result["driver_version"] = version.value
        driver.call("cuDeviceGetCount", C.byref(count))
        result["visible_gpus"] = []
        for ordinal in range(count.value):
            dev, identity = C.c_int(), UUID()
            driver.call("cuDeviceGet", C.byref(dev), ordinal)
            driver.call("cuDeviceGetUuid_v2", C.byref(identity), dev)
            result["visible_gpus"].append({"ordinal": ordinal, "uuid": "GPU-" + str(uuid.UUID(bytes=bytes(identity)))})
        if args.device >= count.value:
            raise ValueError("selected device is not visible")
        result["selected_gpu_uuid"] = result["visible_gpus"][args.device]["uuid"]
        driver.call("cuDeviceGet", C.byref(device), args.device)
        if args.role == "inspect":
            result["ok"] = True
            return 0
        driver.call("cuDevicePrimaryCtxRetain", C.byref(context), device)
        retained = True
        driver.call("cuCtxSetCurrent", context)
        driver.call("cuMemAlloc_v2", C.byref(own), args.bytes)
        result["allocation"] = {"api": "cuMemAlloc_v2", "pointer": hex(own.value), "bytes": args.bytes}
        if args.role == "producer":
            if Path(metadata).exists() or Path(done).exists():
                raise ValueError("producer requires a fresh stem")
            driver.call("cuMemsetD8_v2", own, 73, args.bytes)
            driver.call("cuCtxSynchronize")
            handle = Handle()
            driver.call("cuIpcGetMemHandle", C.byref(handle), own)
            raw = bytes(handle)
            packet = {"session_id": args.session_id, "bytes": args.bytes, "fill_byte": 73,
                      "handle_base64": base64.b64encode(raw).decode(), "handle_sha256": hashlib.sha256(raw).hexdigest(),
                      "allocation": result["allocation"], "producer_pid": os.getpid(),
                      "producer_gpu_uuid": result["selected_gpu_uuid"]}
            exported = True
            atomic_json(metadata, packet)
            result["stage"] = "wait_consumer_closed"
            ack = wait_packet(done, args.session_id, deadline)
            if ack.get("import_closed") is not True:
                raise ValueError("consumer did not confirm no active import")
            safe_export_free = True
            result["consumer_done"] = ack
            if ack.get("ok") is not True:
                raise RuntimeError("consumer reported failure")
        else:
            result["stage"] = "wait_metadata"
            packet = wait_packet(metadata, args.session_id, deadline)
            handle = decode_handle(packet, args.session_id, args.bytes)
            result["producer_gpu_uuid"] = packet["producer_gpu_uuid"]
            result["handle_sha256"] = packet["handle_sha256"]
            driver.call("cuIpcOpenMemHandle", C.byref(imported), handle, 1)
            import_active = True
            driver.call("cuMemcpyDtoD_v2", own, imported, args.bytes)
            driver.call("cuCtxSynchronize")
            host = (C.c_ubyte * args.bytes)()
            driver.call("cuMemcpyDtoH_v2", host, own, args.bytes)
            actual = bytes(host)
            result["verification"] = {"all_bytes_equal_73": actual == bytes([73]) * args.bytes,
                                      "checksum_sum": sum(actual), "expected_sum": 73 * args.bytes,
                                      "sha256": hashlib.sha256(actual).hexdigest()}
            if not result["verification"]["all_bytes_equal_73"]:
                raise ValueError("payload mismatch")
        result["ok"] = True
    except Exception as exc:
        result["errors"].append({"stage": result["stage"], "type": type(exc).__name__, "error": str(exc)})
    finally:
        # Native hangs remain bounded by the coordinator's external timeout.
        signal.setitimer(signal.ITIMER_REAL, 0)
        if driver:
            if import_active and cleanup("cuIpcCloseMemHandle", imported):
                import_active = False
            if own.value and (not exported or safe_export_free):
                cleanup("cuMemFree_v2", own)
            elif own.value:
                result["errors"].append({"stage": "cleanup", "error": "consumer closure unconfirmed; exporter allocation retained until process exit"})
            if retained and (not exported or safe_export_free) and not import_active:
                cleanup("cuDevicePrimaryCtxRelease_v2", device)
        result["ok"] = result["ok"] and not result["errors"]
        result["import_closed"] = not import_active
        result["elapsed_seconds"] = time.monotonic() - started
        signal.signal(signal.SIGALRM, old_handler)
        atomic_json(args.result, result)
        if args.role == "consumer" and not import_active:
            atomic_json(done, {"session_id": args.session_id, "ok": result["ok"], "import_closed": True})
        print(json.dumps(result), flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
