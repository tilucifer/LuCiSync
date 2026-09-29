from __future__ import annotations

import ctypes
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable


APP_NAME = "Synctool"
SCHEMA_VERSION = 1


def app_data_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        return Path(base) / APP_NAME if base else Path.home() / "AppData" / "Roaming" / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME")
    return Path(base) / "synctool" if base else Path.home() / ".config" / "synctool"


DEFAULT_CONFIG = app_data_dir() / "config.json"
DEFAULT_JOURNAL = app_data_dir() / "sync-history.jsonl"


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def save_config(path: Path, destination: str, entries: list[dict]) -> None:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": utc_now(),
        "destination": destination,
        "entries": [
            {
                "id": entry.get("id") or str(uuid.uuid4()),
                "source": entry["source"],
                "target_rel": entry["target_rel"],
            }
            for entry in entries
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_config(path: Path) -> tuple[str, list[dict]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Version de configuration inconnue.")
    raw_entries = payload.get("entries", [])
    if not isinstance(raw_entries, list):
        raise ValueError("La liste des sources est invalide.")
    entries = []
    for row in raw_entries:
        if not isinstance(row, dict) or not row.get("source") or not row.get("target_rel"):
            continue
        entries.append({
            "id": str(row.get("id") or uuid.uuid4()),
            "source": str(row["source"]),
            "target_rel": str(row["target_rel"]),
            "status": "idle",
            "message": "",
        })
    return str(payload.get("destination", "")), entries


def safe_target_relative(value: str) -> str:
    """Normalize a UI target path and reject paths that escape the destination."""
    normalized = value.strip().replace("\\", "/")
    pieces = [piece for piece in normalized.split("/") if piece not in ("", ".")]
    if not pieces or any(piece == ".." for piece in pieces) or normalized.startswith("/"):
        raise ValueError("Le chemin de destination doit rester relatif au dossier choisi.")
    if pieces and ":" in pieces[0]:
        raise ValueError("Un lecteur ne peut pas être indiqué dans le chemin relatif.")
    return "/".join(pieces)


@dataclass(frozen=True)
class Location:
    label: str
    path: str
    kind: str


def _windows_drive_locations() -> list[Location]:
    locations: list[Location] = []
    try:
        get_drive_type = ctypes.windll.kernel32.GetDriveTypeW
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for index in range(26):
            if not bitmask & (1 << index):
                continue
            root = f"{chr(ord('A') + index)}:\\"
            if get_drive_type(ctypes.c_wchar_p(root)) == 4:  # DRIVE_REMOTE
                locations.append(Location(f"Lecteur réseau {root}", root, "SMB"))
    except (AttributeError, OSError):
        pass
    return locations


def _wsl_locations() -> list[Location]:
    executable = shutil.which("wsl.exe") or shutil.which("wsl")
    if not executable:
        return []
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            [executable, "--list", "--quiet"], capture_output=True, timeout=5, check=False,
            creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    raw = result.stdout
    if b"\x00" in raw:
        output = raw.decode("utf-16-le", errors="replace")
    else:
        output = raw.decode("utf-8", errors="replace")
    locations = []
    for name in output.replace("\x00", "").splitlines():
        name = name.strip().lstrip("*").strip()
        if name:
            locations.append(Location(f"WSL · {name}", f"\\\\wsl.localhost\\{name}\\", "WSL"))
    return locations


def _linux_smb_locations() -> list[Location]:
    found: dict[str, Location] = {}
    try:
        lines = Path("/proc/mounts").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    for line in lines:
        fields = line.split()
        if len(fields) < 3 or fields[2].lower() not in {"cifs", "smb3", "smbfs"}:
            continue
        mount = fields[1].replace("\\040", " ").replace("\\011", "\t")
        found[mount] = Location(f"Partage SMB · {mount}", mount, "SMB")
    gvfs = Path(f"/run/user/{os.getuid()}/gvfs") if hasattr(os, "getuid") else None
    if gvfs and gvfs.is_dir():
        for mount in gvfs.glob("smb-share:*"):
            found[str(mount)] = Location(f"Partage SMB · {mount.name}", str(mount), "SMB")
    return list(found.values())


def discover_locations() -> list[Location]:
    if os.name == "nt":
        return _windows_drive_locations() + _wsl_locations()
    if sys.platform.startswith("linux"):
        return _linux_smb_locations()
    return []


@dataclass
class SyncResult:
    entry_id: str
    source: str
    target_rel: str
    status: str
    message: str
    method: str
    started_at: str
    duration_seconds: float


def _same_file(source: Path, destination: Path) -> bool:
    try:
        source_stat = source.stat()
        destination_stat = destination.stat()
        return source_stat.st_size == destination_stat.st_size and source_stat.st_mtime_ns == destination_stat.st_mtime_ns
    except OSError:
        return False


def _copy_resumable(source: Path, destination: Path) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if _same_file(source, destination):
        return False

    source_stat = source.stat()
    partial = destination.with_name(f".{destination.name}.synctool-partial")
    metadata = partial.with_name(partial.name + ".json")
    expected = {
        "source": str(source.resolve()),
        "size": source_stat.st_size,
        "mtime_ns": source_stat.st_mtime_ns,
    }
    offset = 0
    try:
        if metadata.is_file() and partial.is_file():
            saved = json.loads(metadata.read_text(encoding="utf-8"))
            candidate = partial.stat().st_size
            if saved == expected and 0 <= candidate <= source_stat.st_size:
                offset = candidate
        if offset == 0:
            partial.unlink(missing_ok=True)
            metadata.write_text(json.dumps(expected), encoding="utf-8")
        with source.open("rb") as src, partial.open("ab" if offset else "wb") as dst:
            src.seek(offset)
            shutil.copyfileobj(src, dst, length=8 * 1024 * 1024)
        shutil.copystat(source, partial)
        os.replace(partial, destination)
        metadata.unlink(missing_ok=True)
    except Exception:
        # Keep the partial and its source fingerprint so the next run can resume it.
        raise
    return True


def _python_copy(source: Path, target: Path) -> tuple[int, int]:
    if source.is_file():
        copied = int(_copy_resumable(source, target))
        return copied, 1 - copied
    if not source.is_dir():
        raise FileNotFoundError(f"Source introuvable : {source}")
    target.mkdir(parents=True, exist_ok=True)
    copied = skipped = 0
    for root, dirs, files in os.walk(source, followlinks=False):
        root_path = Path(root)
        relative = root_path.relative_to(source)
        dest_dir = target / relative
        dest_dir.mkdir(parents=True, exist_ok=True)
        # Preserve empty directories while avoiding recursive traversal through symlinks.
        for directory in dirs:
            candidate = root_path / directory
            if not candidate.is_symlink():
                (dest_dir / directory).mkdir(parents=True, exist_ok=True)
        for filename in files:
            candidate = root_path / filename
            output = dest_dir / filename
            if candidate.is_file():
                if _copy_resumable(candidate, output):
                    copied += 1
                else:
                    skipped += 1
    return copied, skipped


def _run_rsync(source: Path, target: Path) -> str:
    source_arg = str(source) + (os.sep if source.is_dir() else "")
    command = ["rsync", "-a", "--partial", "--human-readable", "--itemize-changes", source_arg, str(target)]
    completed = subprocess.run(command, capture_output=True, text=True, errors="replace", check=False)
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        raise RuntimeError(detail[-1] if detail else f"rsync a renvoyé le code {completed.returncode}.")
    changed = sum(1 for line in completed.stdout.splitlines() if line.strip())
    return f"rsync terminé · {changed} élément(s) transféré(s) ou mis à jour."


def _run_robocopy(source: Path, target: Path) -> str:
    executable = shutil.which("robocopy")
    if not executable:
        raise FileNotFoundError("robocopy est introuvable.")
    if source.is_dir():
        command = [executable, str(source), str(target), "/E", "/Z", "/FFT", "/COPY:DAT", "/DCOPY:DAT", "/R:2", "/W:1", "/NP", "/NJH", "/NJS"]
    else:
        command = [executable, str(source.parent), str(target.parent), source.name, "/Z", "/FFT", "/COPY:DAT", "/R:2", "/W:1", "/NP", "/NJH", "/NJS"]
    completed = subprocess.run(command, capture_output=True, text=True, errors="replace", check=False)
    if completed.returncode >= 8:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        raise RuntimeError(detail[-1] if detail else f"robocopy a renvoyé le code {completed.returncode}.")
    return f"robocopy terminé (code {completed.returncode})."


def sync_one(entry: dict, destination_root: str) -> SyncResult:
    started_at = utc_now()
    start = time.monotonic()
    source = Path(entry["source"]).expanduser().resolve()
    target_rel = safe_target_relative(entry["target_rel"])
    destination = Path(destination_root).expanduser().resolve()
    target = destination.joinpath(*target_rel.split("/"))
    method = "Python"
    try:
        if not source.exists():
            raise FileNotFoundError(f"Source introuvable : {source}")
        source_resolved = source.resolve()
        target_resolved = target.resolve()
        if source.is_dir() and (target_resolved == source_resolved or source_resolved in target_resolved.parents):
            raise ValueError("La destination de cet élément se trouve dans sa source.")
        if source.is_file() and target_resolved == source_resolved:
            return SyncResult(entry["id"], str(source), target_rel, "success", "Déjà au même emplacement.", "—", started_at, time.monotonic() - start)

        destination.mkdir(parents=True, exist_ok=True)
        if os.name == "nt" and shutil.which("robocopy"):
            method = "robocopy"
            message = _run_robocopy(source, target)
        elif os.name != "nt" and shutil.which("rsync"):
            method = "rsync"
            target.parent.mkdir(parents=True, exist_ok=True)
            message = _run_rsync(source, target)
        else:
            copied, skipped = _python_copy(source, target)
            message = f"{copied} fichier(s) copié(s), {skipped} inchangé(s)."
        return SyncResult(entry["id"], str(source), target_rel, "success", message, method, started_at, time.monotonic() - start)
    except Exception as error:
        return SyncResult(entry["id"], str(source), target_rel, "failure", str(error), method, started_at, time.monotonic() - start)


def append_history(path: Path, result: SyncResult, destination: str, config_path: Path) -> None:
    record = {
        "timestamp": result.started_at,
        "status": result.status,
        "source": result.source,
        "target_rel": result.target_rel,
        "destination": destination,
        "message": result.message,
        "method": result.method,
        "duration_seconds": round(result.duration_seconds, 3),
        "config": str(config_path),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as journal:
        journal.write(json.dumps(record, ensure_ascii=False) + "\n")


def recent_history(path: Path, limit: int = 8) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    records = []
    for line in reversed(lines):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(records) >= limit:
            break
    return records
