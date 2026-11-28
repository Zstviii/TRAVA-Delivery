import re
import shutil
import subprocess
import threading
from collections.abc import Callable


class CloudflareTunnel:
    """Запускает бесплатный Quick Tunnel к локальному Mini App."""

    URL_PATTERN = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")

    def __init__(
        self,
        local_url: str,
        on_url: Callable[[str], None],
        on_status: Callable[[str], None] | None = None,
    ):
        self.local_url = local_url
        self.on_url = on_url
        self.on_status = on_status
        self.process = None
        self.thread = None
        self.public_url = None
        self._stop_event = threading.Event()

    def start(self):
        if self.process is not None:
            return

        executable = shutil.which("cloudflared")
        if executable is None:
            raise FileNotFoundError(
                "Не найден cloudflared. Установите Cloudflare Tunnel и "
                "добавьте cloudflared.exe в PATH."
            )

        self._stop_event.clear()
        options = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
        }
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            options["creationflags"] = subprocess.CREATE_NO_WINDOW

        self.process = subprocess.Popen(
            [
                executable,
                "tunnel",
                "--no-autoupdate",
                "--url",
                self.local_url,
            ],
            **options,
        )
        self.thread = threading.Thread(
            target=self._read_output,
            daemon=True,
            name="TRAVA-Cloudflare-Tunnel",
        )
        self.thread.start()

    def _read_output(self):
        if self.process is None or self.process.stdout is None:
            return

        for line in self.process.stdout:
            if self._stop_event.is_set():
                return

            match = self.URL_PATTERN.search(line)
            if match and self.public_url is None:
                self.public_url = match.group(0)
                self.on_url(self.public_url)
            elif self.on_status and (
                "ERR" in line.upper() or "ERROR" in line.upper()
            ):
                self.on_status("Cloudflare Tunnel сообщает об ошибке. Проверьте интернет.")

        if not self._stop_event.is_set() and self.on_status:
            self.on_status("Cloudflare Tunnel остановился.")

    def close(self):
        self._stop_event.set()
        process = self.process
        self.process = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
