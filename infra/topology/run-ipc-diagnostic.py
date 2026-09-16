#!/usr/bin/env python3
"""Run a bounded raw CUDA IPC diagnosis on one already-provisioned 8-GPU node.

Without --execute this only validates session.json and renders Kubernetes objects.
It never provisions or destroys infrastructure; the pilot-session guard owns cleanup.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid


NAMESPACE = "topology-ipc"
IMAGE = "docker.io/vllm/vllm-openai:v0.26.0@sha256:770fe65b2c73ee74a5c42165cf3433de4048cc2cd9c57a937ca4e35aba5aa87b"
PROBE = Path(__file__).with_name("raw-ipc.py")
RESULT_NAME = "ipc-diagnostic-result.json"


class DiagnosticError(RuntimeError):
    pass


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def pod(name, node, config, gpus):
    container = {
        "name": "probe", "image": IMAGE, "imagePullPolicy": "IfNotPresent",
        "command": ["sleep", "infinity"],
        "resources": {"requests": {"cpu": "1", "memory": "2Gi", "nvidia.com/gpu": str(gpus)},
                      "limits": {"cpu": "4", "memory": "4Gi", "nvidia.com/gpu": str(gpus)}},
        "volumeMounts": [{"name": "probe", "mountPath": "/probe/raw-ipc.py", "subPath": "raw-ipc.py", "readOnly": True},
                         {"name": "shm", "mountPath": "/dev/shm"}],
    }
    return {"apiVersion": "v1", "kind": "Pod",
            "metadata": {"name": name, "namespace": NAMESPACE,
                         "labels": {"app.kubernetes.io/name": "raw-ipc-diagnostic"}},
            "spec": {"nodeSelector": {"kubernetes.io/hostname": node}, "restartPolicy": "Never", "hostIPC": True, "hostPID": True,
                     "terminationGracePeriodSeconds": 1, "containers": [container],
                     "volumes": [{"name": "probe", "configMap": {"name": config}},
                                 {"name": "shm", "hostPath": {"path": "/dev/shm", "type": "Directory"}}]}}


def manifests(node, short):
    config = f"raw-ipc-{short}"
    common = [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NAMESPACE,
         "labels": {"app.kubernetes.io/managed-by": "raw-ipc-diagnostic"}}},
        {"apiVersion": "v1", "kind": "ConfigMap", "immutable": True,
         "metadata": {"name": config, "namespace": NAMESPACE},
         "data": {"raw-ipc.py": PROBE.read_text()}},
    ]
    pods = {
        "control1": pod(f"ipc-control-{short}-1", node, config, 8),
        "producer": pod(f"ipc-producer-{short}", node, config, 1),
        "consumer": pod(f"ipc-consumer-{short}", node, config, 1),
        "control2": pod(f"ipc-control-{short}-2", node, config, 8),
    }
    return common, pods


def probe_exec_args(pod_name, role, device, stem, arm, result, visible_uuid=None):
    """Build kubectl arguments after its context prefix."""
    outer, inner = (("100", "90") if role == "producer" else ("40", "35"))
    command = ["timeout", "-k", "5", outer]
    if visible_uuid:
        command = ["env", f"CUDA_VISIBLE_DEVICES={visible_uuid}", *command]
    command += ["python3", "/probe/raw-ipc.py", role, "--device", str(device), "--stem", stem,
                "--session-id", arm, "--result", result, "--timeout", inner]
    return ["-n", NAMESPACE, "exec", pod_name, "--", *command]


class Runner:
    def __init__(self, args, session, objects):
        self.args, self.session, self.common, self.pods = args, session, *objects
        self.deadline = float(session["cleanup_start_deadline_unix"])
        self.result = {"session_id": session["session_id"], "profile": "ipc-diagnostic",
                       "context": args.context, "node": args.node, "namespace": NAMESPACE,
                       "split_first": bool(getattr(args, "split_first", False)),
                       "started_unix": time.time(), "cases": [], "events": [], "ok": False}
        self.result_path = args.run_dir / RESULT_NAME

    def save(self):
        write_json(self.result_path, self.result)

    def event(self, event, **fields):
        record = {"event": event, "unix": time.time(), **fields}
        self.result["events"].append(record)
        self.save()
        print(json.dumps(record, sort_keys=True), flush=True)

    def remaining(self, minimum=110):
        value = self.deadline - time.time()
        if value < minimum:
            raise DiagnosticError(f"cleanup deadline guard: {value:.1f}s remaining; need {minimum}s")
        return value

    def kubectl(self, arguments, *, timeout=30, input_text=None, check=False):
        # Phase and pair entry points enforce the 110/120-second admission
        # margin. Commands already in flight only need to remain before the
        # absolute cleanup-start deadline.
        self.remaining(3)
        command = ["kubectl", "--context", self.args.context, *arguments]
        limit = max(1, min(timeout, self.deadline - time.time() - 2))
        try:
            completed = subprocess.run(command, input=input_text, text=True, capture_output=True,
                                       timeout=limit, check=False)
        except subprocess.TimeoutExpired as error:
            completed = subprocess.CompletedProcess(command, 124, error.stdout or "", error.stderr or "timeout")
        if check and completed.returncode:
            raise DiagnosticError((completed.stderr or completed.stdout or "kubectl failed").strip())
        return completed

    def apply(self, value):
        self.remaining()
        completed = self.kubectl(["apply", "-f", "-"], input_text=json.dumps(value), timeout=30)
        if completed.returncode:
            raise DiagnosticError(f"apply {value['kind']}/{value['metadata']['name']}: {completed.stderr.strip()}")

    def ready(self, name):
        while True:
            available = self.remaining(120)
            wait_seconds = max(1, min(60, int(available - 115)))
            completed = self.kubectl(["-n", NAMESPACE, "wait", "--for=condition=Ready", f"pod/{name}",
                                      f"--timeout={wait_seconds}s"], timeout=wait_seconds + 5)
            if completed.returncode == 0:
                self.event("pod_ready", pod=name)
                return
            self.event("pod_waiting", pod=name, remaining_seconds=round(self.deadline - time.time(), 1),
                       detail=(completed.stderr or completed.stdout).strip()[-500:])
            time.sleep(1)

    def capture_pod(self, name, label):
        completed = self.kubectl(["-n", NAMESPACE, "get", "pod", name, "-o", "json"], timeout=15)
        path = self.args.run_dir / "ipc-diagnostic-pods" / f"{label}.json"
        if completed.returncode == 0:
            try:
                write_json(path, json.loads(completed.stdout))
            except json.JSONDecodeError:
                path.write_text(completed.stdout)
        else:
            write_json(path, {"error": completed.stderr.strip(), "pod": name})

    def delete(self, names, label):
        self.remaining()
        for name in names:
            self.capture_pod(name, f"{label}-{name}")
        submitted = self.kubectl(["-n", NAMESPACE, "delete", "pod", *names,
                                  "--wait=false", "--ignore-not-found=true"], timeout=15)
        last_detail = submitted.stderr.strip() or None
        while True:
            available = self.deadline - time.time()
            if available <= 110:
                raise DiagnosticError(
                    f"pods still deleting at next-case guard: {', '.join(names)}; {last_detail or 'no detail'}"
                )
            observed = self.kubectl(["-n", NAMESPACE, "get", "pod", *names,
                                     "--ignore-not-found=true", "-o", "name"],
                                    timeout=max(1, min(5, available - 110)))
            if observed.returncode == 0 and not observed.stdout.strip():
                self.event("pods_deleted", pods=names, submit_returncode=submitted.returncode,
                           submit_error=submitted.stderr.strip() or None)
                return
            last_detail = (observed.stderr or observed.stdout).strip() or last_detail
            self.event("pods_deleting", pods=names, remaining_seconds=round(available, 1),
                       detail=last_detail[-500:] if last_detail else None)
            time.sleep(min(2, max(0, available - 110)))

    def remote_json(self, pod_name, path, timeout=8):
        completed = self.kubectl(["-n", NAMESPACE, "exec", pod_name, "--", "cat", path], timeout=timeout)
        if completed.returncode:
            return None
        try:
            value = json.loads(completed.stdout)
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None

    def copy_json(self, pod_name, path, value):
        code = ("import os,pathlib,sys; p=pathlib.Path(sys.argv[1]); "
                "t=p.with_name(p.name+'.copy.tmp'); t.write_text(sys.stdin.read()); os.replace(t,p)")
        completed = self.kubectl(["-n", NAMESPACE, "exec", "-i", pod_name, "--",
                                  "python3", "-c", code, path], input_text=json.dumps(value) + "\n", timeout=5)
        if completed.returncode:
            raise DiagnosticError(f"copy to {pod_name}:{path}: {completed.stderr.strip()}")

    def inspect(self, pod_name, label):
        self.remaining()
        stem = f"/tmp/ipc-{uuid.uuid4().hex}"
        path = stem + ".inspect.json"
        arm = f"{self.session['session_id']}-{label}"
        command = ["-n", NAMESPACE, "exec", pod_name, "--", "timeout", "-k", "5", "30",
                   "python3", "/probe/raw-ipc.py", "inspect", "--device", "0", "--stem", stem,
                   "--session-id", arm, "--result", path, "--timeout", "20"]
        completed = self.kubectl(command, timeout=35)
        value = self.remote_json(pod_name, path) or {"ok": False, "errors": [{"error": "missing inspect result"}]}
        value["exec_returncode"] = completed.returncode
        write_json(self.args.run_dir / "ipc-diagnostic-inspect" / f"{label}.json", value)
        self.event("inspect", label=label, pod=pod_name, ok=value.get("ok", False))
        return value

    def stop_role(self, pod_name, arm, role):
        # Python is known to exist in the pinned image; procps utilities are not
        # assumed. Match distinct argv fields, never arbitrary command text.
        code = """import os,signal,sys,time,json
from pathlib import Path
role, arm = sys.argv[1:]
def matches():
    result=[]
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name)==os.getpid(): continue
        try: args=(entry/'cmdline').read_bytes().decode(errors='replace').split('\\0')
        except OSError: continue
        if not any(Path(a).name=='raw-ipc.py' for a in args): continue
        if role not in args or '--session-id' not in args: continue
        index=args.index('--session-id')
        if index+1<len(args) and args[index+1]==arm: result.append(int(entry.name))
    return result
for sig, seconds in [(signal.SIGTERM,2),(signal.SIGKILL,5)]:
    for pid in matches():
        try: os.kill(pid,sig)
        except ProcessLookupError: pass
    deadline=time.monotonic()+seconds
    while matches() and time.monotonic()<deadline: time.sleep(.1)
    if not matches(): break
remaining=matches()
print(json.dumps({'remaining_pids':remaining}))
sys.exit(1 if remaining else 0)
"""
        completed = self.kubectl(["-n", NAMESPACE, "exec", pod_name, "--",
                                  "python3", "-c", code, role, arm], timeout=12)
        return {"returncode": completed.returncode, "confirmed_stopped": completed.returncode == 0,
                "stdout": completed.stdout.strip(), "stderr": completed.stderr.strip()}

    def run_case(self, name, producer_pod, consumer_pod, producer_device, consumer_device,
                 producer_uuid=None, consumer_uuid=None):
        self.remaining()
        case_dir = self.args.run_dir / "ipc-diagnostic-cases" / name
        arm = f"{self.session['session_id']}-{name}-{uuid.uuid4().hex[:8]}"
        stem = f"/tmp/ipc-{uuid.uuid4().hex}"
        producer_result, consumer_result = stem + ".producer.json", stem + ".consumer.json"
        record = {"name": name, "status": "FAIL", "producer_pod": producer_pod,
                  "consumer_pod": consumer_pod, "producer_device": producer_device,
                  "consumer_device": consumer_device, "producer_visible_uuid": producer_uuid,
                  "consumer_visible_uuid": consumer_uuid, "session_id": arm, "stem": stem,
                  "errors": []}
        self.event("case_started", case=name)
        producer = None
        producer_started = time.monotonic()
        consumer_started = False
        safe_ack = False
        try:
            producer_args = probe_exec_args(producer_pod, "producer", producer_device, stem, arm,
                                            producer_result, producer_uuid)
            producer = subprocess.Popen(["kubectl", "--context", self.args.context, *producer_args],
                                        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            producer_started = time.monotonic()
            metadata = None
            metadata_deadline = time.monotonic() + 15
            while time.monotonic() < metadata_deadline:
                metadata = self.remote_json(producer_pod, stem + ".metadata.json",
                                            timeout=max(0.2, min(2, metadata_deadline - time.monotonic())))
                if metadata:
                    break
                time.sleep(min(0.5, max(0, metadata_deadline - time.monotonic())))
            if not metadata:
                record["errors"].append("producer metadata did not appear within 15 seconds")
            else:
                write_json(case_dir / "metadata.json", metadata)
                if producer_pod != consumer_pod:
                    self.copy_json(consumer_pod, stem + ".metadata.json", metadata)
                consumer_args = probe_exec_args(consumer_pod, "consumer", consumer_device, stem, arm,
                                                consumer_result, consumer_uuid)
                consumer_started = True
                completed = self.kubectl(consumer_args, timeout=45)
                record["consumer_exec"] = {"returncode": completed.returncode,
                                           "stdout": completed.stdout, "stderr": completed.stderr}
                consumer = self.remote_json(consumer_pod, consumer_result, timeout=5)
                done = self.remote_json(consumer_pod, stem + ".done.json", timeout=5)
                if consumer:
                    write_json(case_dir / "consumer.json", consumer)
                record["consumer"] = consumer
                if done and done.get("session_id") == arm and done.get("import_closed") is True:
                    write_json(case_dir / "done.json", done)
                    if producer_pod != consumer_pod:
                        self.copy_json(producer_pod, stem + ".done.json", done)
                    safe_ack = True
                else:
                    record["errors"].append("missing or unsafe consumer acknowledgement")
        except Exception as error:
            record["errors"].append(f"{type(error).__name__}: {error}")
        finally:
            if consumer_started and not safe_ack:
                try:
                    record["consumer_termination"] = self.stop_role(consumer_pod, arm, "consumer")
                    if not record["consumer_termination"]["confirmed_stopped"]:
                        record["errors"].append("consumer process stop could not be confirmed")
                except Exception as error:
                    record["errors"].append(f"consumer stop failed: {error}")
            if producer is not None:
                coordinator_deadline = min(producer_started + 110, time.monotonic() + self.deadline - time.time() - 1)
                try:
                    stdout, stderr = producer.communicate(timeout=max(0.2, coordinator_deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    producer.kill()
                    stdout, stderr = producer.communicate()
                    record["errors"].append("producer exec exceeded its 110-second coordinator bound")
                    try:
                        record["producer_termination"] = self.stop_role(producer_pod, arm, "producer")
                    except Exception as error:
                        record["errors"].append(f"producer stop failed: {error}")
                record["producer_exec"] = {"returncode": producer.returncode, "stdout": stdout, "stderr": stderr}
            try:
                produced = self.remote_json(producer_pod, producer_result, timeout=5)
            except Exception as error:
                produced = None
                record["errors"].append(f"producer result collection failed: {error}")
            if produced:
                write_json(case_dir / "producer.json", produced)
            record["producer"] = produced
        consumer = record.get("consumer")
        if (producer is not None and producer.returncode == 0 and
                record.get("consumer_exec", {}).get("returncode") == 0 and
                produced and produced.get("ok") and consumer and consumer.get("ok") and not record["errors"]):
            record["status"] = "PASS"
        self.result["cases"].append(record)
        write_json(case_dir / "case.json", record)
        self.event("case_finished", case=name, status=record["status"])
        return record

    def limitation(self, name, explanation):
        record = {"name": name, "status": "LIMITATION", "explanation": explanation}
        self.result["cases"].append(record)
        self.event("case_finished", case=name, status="LIMITATION", explanation=explanation)

    def execute_split_first(self):
        for key in ("producer", "consumer"):
            self.apply(self.pods[key])
        split_names = [self.pods[key]["metadata"]["name"] for key in ("producer", "consumer")]
        for name in split_names:
            self.ready(name)
        p_inspect = self.inspect(split_names[0], "split-producer")
        c_inspect = self.inspect(split_names[1], "split-consumer")
        split = self.run_case("c-split-pods", split_names[0], split_names[1], 0, 0)
        producer_uuid = p_inspect.get("selected_gpu_uuid")
        consumer_uuid = c_inspect.get("selected_gpu_uuid")
        self.delete(split_names, "before-control")
        if (not p_inspect.get("ok") or not c_inspect.get("ok") or not producer_uuid or
                not consumer_uuid or producer_uuid == consumer_uuid):
            self.limitation("b-matched-pair", "split pods did not expose a distinct physical GPU pair for control retest")
            return

        control = self.pods["control1"]
        control_name = control["metadata"]["name"]
        self.apply(control)
        self.ready(control_name)
        control_inspect = self.inspect(control_name, "control-1")
        if not control_inspect.get("ok") or len(control_inspect.get("visible_gpus", [])) != 8:
            raise DiagnosticError("control pod did not expose exactly eight GPUs")
        ordinal_by_uuid = {item.get("uuid"): item.get("ordinal") for item in control_inspect["visible_gpus"]}
        if producer_uuid not in ordinal_by_uuid or consumer_uuid not in ordinal_by_uuid:
            self.limitation("b-matched-pair", "split-pod physical GPU pair could not be mapped in the control pod")
            return
        producer_ordinal, consumer_ordinal = ordinal_by_uuid[producer_uuid], ordinal_by_uuid[consumer_uuid]
        same = self.run_case("a-same-gpu", control_name, control_name, producer_ordinal, producer_ordinal)
        if same["status"] != "PASS":
            return
        matched = self.run_case("b-matched-pair", control_name, control_name,
                                producer_ordinal, consumer_ordinal)
        if matched["status"] != "PASS":
            return
        if split["status"] != "PASS":
            self.run_case("b2-matched-cvd", control_name, control_name, 0, 0,
                          producer_uuid, consumer_uuid)

    def execute(self):
        try:
            self.remaining(120)
            for item in self.common:
                self.apply(item)
            if getattr(self.args, "split_first", False):
                self.execute_split_first()
                return
            control1 = self.pods["control1"]
            self.apply(control1)
            self.ready(control1["metadata"]["name"])
            first = self.inspect(control1["metadata"]["name"], "control-1")
            if not first.get("ok") or len(first.get("visible_gpus", [])) != 8:
                raise DiagnosticError("control pod did not expose exactly eight GPUs")
            a = self.run_case("a-same-gpu", control1["metadata"]["name"], control1["metadata"]["name"], 0, 0)
            if a["status"] != "PASS":
                return
            b = self.run_case("b-cross-gpu", control1["metadata"]["name"], control1["metadata"]["name"], 0, 1)
            if b["status"] != "PASS":
                return
            self.delete([control1["metadata"]["name"]], "before-split")
            for key in ("producer", "consumer"):
                self.apply(self.pods[key])
            split_names = [self.pods[key]["metadata"]["name"] for key in ("producer", "consumer")]
            for name in split_names:
                self.ready(name)
            p_inspect = self.inspect(split_names[0], "split-producer")
            c_inspect = self.inspect(split_names[1], "split-consumer")
            c = self.run_case("c-split-pods", split_names[0], split_names[1], 0, 0)
            if c["status"] == "PASS":
                return
            producer_uuid = p_inspect.get("selected_gpu_uuid")
            consumer_uuid = c_inspect.get("selected_gpu_uuid")
            self.delete(split_names, "before-control-retest")
            control2 = self.pods["control2"]
            self.apply(control2)
            self.ready(control2["metadata"]["name"])
            retry_inspect = self.inspect(control2["metadata"]["name"], "control-2")
            ordinal_by_uuid = {item.get("uuid"): item.get("ordinal") for item in retry_inspect.get("visible_gpus", [])}
            if (not retry_inspect.get("ok") or not producer_uuid or not consumer_uuid or
                    producer_uuid == consumer_uuid or producer_uuid not in ordinal_by_uuid or
                    consumer_uuid not in ordinal_by_uuid):
                self.limitation("b-matched-pair", "split-pod physical GPU pair could not be mapped in the recreated control pod")
                return
            matched = self.run_case("b-matched-pair", control2["metadata"]["name"], control2["metadata"]["name"],
                                    ordinal_by_uuid[producer_uuid], ordinal_by_uuid[consumer_uuid])
            if matched["status"] != "PASS":
                return
            self.run_case("b2-matched-cvd", control2["metadata"]["name"], control2["metadata"]["name"],
                          0, 0, producer_uuid, consumer_uuid)
        except Exception as error:
            self.result["errors"] = self.result.get("errors", []) + [f"{type(error).__name__}: {error}"]
            self.event("orchestrator_error", error=str(error), error_type=type(error).__name__)
        finally:
            self.result["finished_unix"] = time.time()
            failures = [case for case in self.result["cases"] if case["status"] == "FAIL"]
            self.result["ok"] = not failures and not self.result.get("errors") and bool(self.result["cases"])
            self.save()
            print(json.dumps({"event": "diagnostic_finished", "ok": self.result["ok"],
                              "result": str(self.result_path)}, sort_keys=True), flush=True)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True, help="existing kubectl context; KUBECONFIG is inherited")
    parser.add_argument("--node", required=True, help="existing eight-GPU Kubernetes node name")
    parser.add_argument("--run-dir", required=True, type=Path, help="pilot run directory containing session.json")
    parser.add_argument("--split-first", action="store_true",
                        help="run split-pod case C before same-container controls on its exact GPU pair")
    parser.add_argument("--execute", action="store_true", help="apply objects and run probes; omission is render-only")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        session = json.loads((args.run_dir / "session.json").read_text())
        if session.get("profile") != "ipc-diagnostic":
            raise DiagnosticError("session.json profile must be ipc-diagnostic")
        if not isinstance(session.get("session_id"), str) or not session["session_id"]:
            raise DiagnosticError("session.json lacks a nonempty session_id")
        if not isinstance(session.get("cleanup_start_deadline_unix"), (int, float)):
            raise DiagnosticError("session.json lacks cleanup_start_deadline_unix")
        short = hashlib.sha256(str(session.get("session_id", "")).encode()).hexdigest()[:8]
        objects = manifests(args.node, short)
        preview = args.run_dir / "ipc-diagnostic-manifests.json"
        write_json(preview, [*objects[0], *objects[1].values()])
        if not args.execute:
            print(json.dumps({"execute": False, "kubernetes_calls": 0, "manifests": str(preview)}, sort_keys=True))
            return 0
        runner = Runner(args, session, objects)
        runner.execute()
        return 0 if runner.result["ok"] else 1
    except (OSError, ValueError, json.JSONDecodeError, DiagnosticError) as error:
        print(json.dumps({"error": str(error), "type": type(error).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
