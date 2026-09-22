"""Linux process tests for detached engine workers and strict epoch scoping."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('layout_engines', HERE/'layout-engines.py')
layout = importlib.util.module_from_spec(spec); spec.loader.exec_module(layout)


@unittest.skipUnless(sys.platform == 'linux', 'Requires real Linux /proc')
class ShutdownTests(unittest.TestCase):
    def test_detached_worker_survives_parent_but_is_drained_without_other_epoch(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            other = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                env={**os.environ, 'TP4_RESULTS_DIR':str(out/'other')})
            worker_code = ('import signal,time,pathlib,os; '
                'signal.signal(signal.SIGTERM,signal.SIG_IGN); '
                f'pathlib.Path({str(out/"ready")!r}).write_text(str(os.getpid())); '
                'time.sleep(60)')
            parent_code = ('import subprocess,sys; '
                f'subprocess.Popen([sys.executable,"-c",{worker_code!r}],start_new_session=True)')
            worker_pid = None
            try:
                subprocess.run([sys.executable,'-c',parent_code],check=True,
                    env={**os.environ,'TP4_RESULTS_DIR':str(out)})
                until = time.monotonic()+5
                while not (out/'ready').exists() and time.monotonic()<until: time.sleep(.05)
                worker_pid = int((out/'ready').read_text())
                self.assertIn(worker_pid, layout.epoch_processes(out))
                layout.drain_epoch(out, timeout=5, kill_after=.2, interval=.05, gpu_query=lambda:'')
                self.assertFalse(layout.epoch_processes(out))
                self.assertIsNone(other.poll())
                proof = json.loads((out/'shutdown.json').read_text())
                self.assertTrue(proof['drained'])
                self.assertTrue(any(x['signal']==signal.SIGKILL for x in proof['signals']))
            finally:
                other.kill(); other.wait()
                if worker_pid in layout.epoch_processes(out): os.kill(worker_pid,signal.SIGKILL)

    def test_waits_for_gpu_cleanup_and_rejects_persistent_gpu_state(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory); samples=iter(['host-pid','host-pid',''])
            layout.drain_epoch(out,timeout=2,interval=.01,gpu_query=lambda:next(samples))
            self.assertTrue(json.loads((out/'shutdown.json').read_text())['drained'])
            with self.assertRaisesRegex(RuntimeError,'bounded shutdown'):
                layout.drain_epoch(out,timeout=.05,interval=.01,gpu_query=lambda:'unknown-host-pid')
            self.assertFalse(json.loads((out/'shutdown.json').read_text())['drained'])


if __name__ == '__main__': unittest.main()
