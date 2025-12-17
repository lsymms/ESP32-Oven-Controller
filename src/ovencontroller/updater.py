"""OTA update helper for the oven controller."""

import json
import os
import ssl
import time

import adafruit_requests
import socketpool
import wifi

from .logger import logger


class OTAUpdater:
    """Handle Wi-Fi connection and manifest-driven updates for the oven controller."""

    def __init__(
        self,
        *,
        settings_path,
        version_url,
        manifest_url,
        file_base_url,
        local_version_path,
        target_folder,
        status_callback,
        message_callback,
    ):
        self.settings_path = settings_path
        self.version_url = version_url
        self.manifest_url = manifest_url
        self.file_base_url = file_base_url.rstrip("/") + "/"
        self.local_version_path = local_version_path
        self.target_folder = target_folder
        self._status_callback = status_callback or (lambda _value: None)
        self._message_callback = message_callback or (lambda _text: None)
        self._requests = None
        self._retries = 1
        self._retry_delay = 1.0

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def read_local_version(self):
        try:
            with open(self.local_version_path, "r") as file:
                return file.read().strip()
        except OSError:
            return "0"

    def check_for_update(self, *, current_version, force=False):
        if not self._ensure_session():
            logger.error("WiFi unavailable for update")
            self._set_status("NOWF")
            self._message("WiFi unavailable for update")
            return False
        try:
            logger.info(f"Fetching version from {self.version_url}")
            response = self._http_get(self.version_url, "version file")
            remote_version = response.text.strip()
            response.close()
        except Exception as error:  # noqa: BLE001
            logger.error("Error fetching version:", error)
            self._set_status("ERR!")
            self._message(f"Update error fetching version: {error}")
            return False
        if not remote_version:
            logger.error("No version data")
            self._set_status("NODT")
            self._message("Update error: no version data")
            return False
        if remote_version == current_version and not force:
            self._set_status("CURR")
            self._message("up to date")
            return False

        manifest = self._download_manifest()
        if not manifest:
            logger.error("invalid manifest")
            self._set_status("ERR!")
            self._message("Update error: invalid manifest")
            return False
        files = manifest.get("files") or []
        logger.info("Updating to version", remote_version, "with", len(files), "files")
        self._set_status(remote_version)
        self._message(
            f"updating from {current_version or 'unknown'} to {remote_version}"
        )
        try:
            self._download_files(files)
        except Exception as error:  # noqa: BLE001
            self._set_status("ERR!")
            self._message(f"Update error: {error}")
            return False

        self._write_local_version(remote_version)
        self._message(f"Update to {remote_version} complete. Rebooting...")
        return remote_version

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _set_status(self, value):
        self._status_callback(value)

    def _message(self, text):
        logger.info("Update message:", text)
        self._message_callback(text)

    def _parse_settings(self):
        ssid = ""
        password = ""
        try:
            with open(self.settings_path, "r") as file:
                for raw_line in file:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip()
                    if value.startswith('"') and value.endswith('"'):
                        value = value[1:-1]
                    if key == "CIRCUITPY_WIFI_SSID":
                        ssid = value
                    elif key == "CIRCUITPY_WIFI_PASSWORD":
                        password = value
        except OSError:
            pass
        return ssid, password

    def _ensure_session(self):
        if self._requests is not None:
            return True
        ssid, password = self._parse_settings()
        if not ssid or not password:
            return False
        try:
            logger.info("connecting to wifi")
            wifi.radio.connect(ssid, password)
            pool = socketpool.SocketPool(wifi.radio)
            self._requests = adafruit_requests.Session(
                pool, ssl.create_default_context()
            )
            self._message("wifi connected")
            try:
                ip = wifi.radio.ipv4_address
                logger.info(f"wifi connected as {ip}")
            except Exception:  # noqa: BLE001
                pass          
            return True
        except Exception as error:  # noqa: BLE001
            self._message(f"WiFi connect failed: {error}")
            logger.error("WiFi connect failed:", error)
            return False

    def _download_manifest(self):
        try:
            logger.info("Downloading manifest from", self.manifest_url)
            response = self._http_get(self.manifest_url, "manifest")
            text = response.text
            response.close()
            return json.loads(text)
        except Exception as error:  # noqa: BLE001
            self._message(f"Manifest error: {error}")
            logger.error("Manifest download error:", error)
            return None

    def _download_files(self, manifest_files):
        for entry in manifest_files:
            path = entry.get("path")
            if not path:
                continue
            url = self._file_url(path)
            target_path = self._join_target(path)
            directory = self._directory_name(target_path)
            self._ensure_directory(directory)
            logger.info("Update download:", path, "from", url)
            try:
                response = self._http_get(url, f"file {path}")
                with open(target_path, "wb") as target:
                    target.write(response.content)
                response.close()
            except Exception as error:  # noqa: BLE001
                raise RuntimeError(f"{path} download failed: {error}")

    def _join_target(self, relative_path):
        if not relative_path:
            return self.target_folder
        parts = [part for part in relative_path.split("/") if part]
        combined = self.target_folder.rstrip("/")
        for part in parts:
            combined = f"{combined}/{part}"
        return combined

    def _directory_name(self, path):
        if not path or path == "/":
            return "/"
        if path.endswith("/"):
            path = path[:-1]
        parts = path.split("/")
        return "/".join(parts[:-1]) or "/"

    def _ensure_directory(self, path):
        if not path:
            return
        full_path = path
        if not full_path.startswith("/"):
            full_path = f"{self.target_folder.rstrip('/')}/{full_path}"
        segments = full_path.split("/")
        current = ""
        for segment in segments:
            if not segment:
                continue
            current = f"{current}/{segment}" if current else f"/{segment}"
            try:
                os.mkdir(current)
            except OSError:
                pass

    def _file_url(self, relative_path):
        clean = relative_path.lstrip("/")
        return f"{self.file_base_url}{clean}"

    def _write_local_version(self, version):
        try:
            with open(self.local_version_path, "w") as file:
                file.write(version)
        except OSError as error:
            logger.error("Failed to update local version:", error)

    def _http_get(self, url, label):
        logger.info("HTTP GET:", label, url)
        """Wrapper that retries GET requests and raises with context."""
        last_error = None
        for attempt in range(1, self._retries + 1):
            try:
                response = self._requests.get(url)
                status = getattr(response, "status_code", 200)
                if status not in (None, 200):
                    response.close()
                    raise RuntimeError(f"{label} HTTP {status}")
                return response
            except Exception as error:  # noqa: BLE001
                last_error = error
                if attempt >= self._retries:
                    break
                self._message(
                    f"{label} attempt {attempt} failed: {error}; retrying"
                )
                time.sleep(self._retry_delay)
        raise last_error or RuntimeError(f"{label} failed")
