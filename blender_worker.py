# blender_worker.py — Off-thread Blender SSH worker that interpolates positions via paramiko.
# Debug entry point: check _do_ssh() SSH connection, remote command stdout/stderr, and lofname exists.
# External deps: none (SSH to BLENDER_HOST; no EPICS PVs).

import os

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from position_io import normalize_positions


class _BlenderWorker(QObject):
    """Runs Blender SSH job off the main thread."""
    finished = pyqtSignal(object)   # list[dict] | None
    error    = pyqtSignal(str)

    def __init__(self, cfg: dict, positions: list, spacing: float, data_dir: str):
        super().__init__()
        self._cfg      = cfg
        self._positions = positions
        self._spacing  = spacing
        self._data_dir = data_dir

    @pyqtSlot()
    def run(self):
        try:
            result = self._do_ssh()
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))

    def _do_ssh(self):
        import paramiko as _pm
        cfg          = self._cfg
        local_mount  = cfg.get("LOCAL_MOUNT", "")
        remote_mount = cfg.get("REMOTE_MOUNT", "")

        temp_pos = os.path.join(self._data_dir, "temp_blender_in.pos")
        with open(temp_pos, 'w') as f:
            f.write("# x y z\n")
            for p in normalize_positions(self._positions):
                f.write(f"{float(p['x']):.6f} {float(p['y']):.6f} {float(p['z']):.6f}\n")

        temp_out = os.path.join(self._data_dir, "temp_blender_out.csv")
        lifname  = os.path.abspath(temp_pos)
        lofname  = os.path.abspath(temp_out)
        ifname   = lifname.replace(local_mount, remote_mount).replace('\\', '/')
        ofname   = lofname.replace(local_mount, remote_mount).replace('\\', '/')

        blender_exe    = cfg.get("BLENDER_EXE", "blender") or "blender"
        blender_script = cfg.get("BLENDER_SCRIPT", "")
        cmd = " ".join([blender_exe, '--background', '--python', blender_script,
                        '--', ifname, ofname, f"{self._spacing:.2f}"])

        client = _pm.SSHClient()
        client.set_missing_host_key_policy(_pm.AutoAddPolicy())
        try:
            client.connect(cfg.get("BLENDER_HOST",""), port=22,
                           username=cfg.get("BLENDER_USER",""),
                           key_filename=cfg.get("BLENDER_KEY",""))
            _stdin, stdout, stderr = client.exec_command(cmd)
            out = stdout.read().decode(); err = stderr.read().decode()
            if out: print(out)
            if err: print("stderr:", err)
            if not os.path.exists(lofname):
                try:
                    sftp = client.open_sftp()
                    sftp.get(ofname, lofname)
                    sftp.close()
                except Exception:
                    pass
        finally:
            client.close()

        if not os.path.exists(lofname):
            return None

        parsed_rows = []
        with open(lofname, newline='', encoding='utf-8', errors='replace') as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith('#'):
                    continue
                parts = [x.strip() for x in stripped.split(',')]
                if len(parts) < 3:
                    continue
                try:
                    parsed_rows.append({
                        "x": float(parts[0]),
                        "y": float(parts[1]),
                        "z": float(parts[2]),
                    })
                except ValueError:
                    continue
        return parsed_rows if parsed_rows else None
