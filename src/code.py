import os
import time
import traceback

import microcontroller

OVEN_DIR = "/ovencontroller"
BACKUP_DIR = "/ovencontroller_backup"
VERSION_FILE = f"{OVEN_DIR}/version.txt"


def _path_join(base, name):
    if base.endswith("/"):
        return base + name
    return base + "/" + name


def _is_dir(path):
    try:
        mode = os.stat(path)[0]
    except OSError:
        return False
    # stat bit for directory on CircuitPython
    return (mode & 0x4000) == 0x4000


def _ensure_folder(path):
    try:
        os.mkdir(path)
    except OSError:
        pass


def _path_exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _remove_tree(path):
    try:
        entries = os.listdir(path)
    except OSError:
        return
    for name in entries:
        full = _path_join(path, name)
        if _is_dir(full):
            _remove_tree(full)
            try:
                os.rmdir(full)
            except OSError:
                pass
        else:
            try:
                os.remove(full)
            except OSError:
                pass


def _copy_tree(src, dst):
    _ensure_folder(dst)
    try:
        entries = os.listdir(src)
    except OSError:
        return
    for name in entries:
        src_path = _path_join(src, name)
        dst_path = _path_join(dst, name)
        if _is_dir(src_path):
            _copy_tree(src_path, dst_path)
        else:
            with open(src_path, "rb") as source, open(dst_path, "wb") as target:
                target.write(source.read())


def _read_version():
    try:
        with open(VERSION_FILE, "r") as file:
            return file.read().strip()
    except OSError:
        return "0.0.0"


def _parse_version(version):
    try:
        parts = [int(part) for part in version.split(".")]
        while len(parts) < 3:
            parts.append(0)
        return parts[:3]
    except ValueError:
        return [0, 0, 0]


def _maybe_refresh_backup(version_text):
    parts = _parse_version(version_text)
    # Only refresh when patch == 0 (known good minor releases)
    if parts[2] != 0:
        return
    print("Updating minor-version backup to", version_text)
    if not _path_exists(BACKUP_DIR):
        _ensure_folder(BACKUP_DIR)
    _remove_tree(BACKUP_DIR)
    _copy_tree(OVEN_DIR, BACKUP_DIR)


def _restore_backup():
    try:
        os.listdir(BACKUP_DIR)
    except OSError:
        print("Backup folder missing; cannot restore.")
        return False
    print("Restoring ovencontroller from backup...")
    _remove_tree(OVEN_DIR)
    _copy_tree(BACKUP_DIR, OVEN_DIR)
    return True


def _run_controller():
    from ovencontroller import ovencontroller

    version_text = _read_version()
    _maybe_refresh_backup(version_text)
    ovencontroller.run()


try:
    _run_controller()
except Exception as error:
    print("Startup error:", error)
    traceback.print_exception(type(error), error, error.__traceback__)
    if _restore_backup():
        time.sleep(1)
        microcontroller.reset()
    else:
        raise
