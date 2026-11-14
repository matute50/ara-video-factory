"""
Sube videos finales de Ara a Google Drive usando rclone.
Crea la carpeta raíz "Ara Videos Saladillo Vivo" automáticamente si no existe.
"""
from __future__ import annotations
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Optional


REMOTE_NAME: str = os.environ.get("RCLONE_REMOTE", "drive:")
DRIVE_ROOT: str = os.environ.get(
    "DRIVE_ROOT_FOLDER",
    "Ara Videos Saladillo Vivo",
)
RETRY_COUNT: int = int(os.environ.get("GDRIVE_RETRY", "2"))
RETRY_DELAY: float = float(os.environ.get("GDRIVE_RETRY_DELAY", "3"))


def _remote_path(remote: str, *parts: str) -> str:
    """Concatena el remote (sin doble colon) con los paths relativos.

    remote puede venir como 'drive:' o 'drive' — se normaliza a una sola
    forma con ':' al final y se unen los parts con '/'.
    """
    base = remote.rstrip(":") + ":"
    cleaned = "/".join(p.strip("/") for p in parts if p)
    return f"{base}{cleaned}" if cleaned else base


def _run_rclone(args: list[str], timeout: int = 120) -> tuple[int, str, str]:
    """Ejecuta un comando rclone y retorna (rc, stdout, stderr)."""
    env = {**os.environ, "RCLONE_CONFIG_NONE": "true"}
    cmd = ["rclone", *args]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, env=env,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        return 1, "", "rclone no instalado — ejecutar: sudo apt install rclone"
    except subprocess.TimeoutExpired:
        return 1, "", "Tiempo de espera agotado"


def _ensure_root_folder(remote: str, folder_name: str) -> str:
    """Crea o identifica la carpeta raíz en Drive. Retorna el path completo."""
    # Verificar si ya existe buscando por nombre
    rc, out, err = _run_rclone([
        "lsjson", _remote_path(remote, folder_name), "--max-depth", "1"
    ], timeout=30)
    if rc == 0:
        # La carpeta existe
        return folder_name

    # Intentar crearla (mkdir recursivo)
    rc, out, err = _run_rclone(["mkdir", _remote_path(remote, folder_name)], timeout=30)
    if rc == 0:
        print(f"[Drive] Carpeta '{folder_name}' creada.")
        return folder_name

    # Si mkdir falló porque ya existe pero lsjson también falló,
    # es posible que tengamos un error de permisos o nombre ambiguo.
    print(f"[Drive] Advertencia al acceder a '{folder_name}': {err or out}")
    return folder_name


def _make_path(folder_name: str, day_folder: str, filename: str) -> str:
    """Construye la ruta relativa dentro del remote: raiz/YYYY-MM-DD/archivo.mp4.

    Cada día tiene su carpeta para poder eliminar videos viejos con facilidad
    borrando la carpeta completa del día.
    """
    return f"{folder_name}/{day_folder}/{filename}"


def upload_video_to_drive(
    video_path: str,
    article_id: int,
    tenor: str = "informativo",
    remote: str = REMOTE_NAME,
    root_folder: str = DRIVE_ROOT,
) -> dict:
    """
    Sube un video final a Google Drive.

    Args:
        video_path: Ruta local absoluta del video MP4.
        article_id: ID del artículo para naming de archivo.
        tenor: Categoría del tenor (usado en logging, no en Drive).
        remote: Remote rclone (default desde env).
        root_folder: Nombre de la carpeta raíz en Drive.

    Returns:
        Dict con 'ok' (bool), 'remote_url' (str|None), 'error' (str|None).
    """
    local_file = Path(video_path)
    if not local_file.exists():
        return {"ok": False, "remote_url": None, "error": f"Archivo no encontrado: {video_path}"}

    file_size_mb = local_file.stat().st_size / 1e6
    print(f"[Drive] Iniciando subida: {local_file.name} ({file_size_mb:.1f} MB)")

    # Preparar naming
    today = time.strftime("%Y-%m-%d")
    safe_title = "".join(c if c.isalnum() or c in "-_ " else "_" for c in local_file.stem)[:40]
    drive_filename = f"{article_id}_{safe_title}.mp4"
    day_folder = time.strftime("%Y-%m-%d")
    remote_path = _make_path(root_folder, day_folder, drive_filename)

    # Asegurar carpeta raíz
    _ensure_root_folder(remote, root_folder)

    # Subir con reintentos
    last_err = ""
    for attempt in range(1, RETRY_COUNT + 1):
        rc, stdout, stderr = _run_rclone([
            "copyto", str(local_file), _remote_path(remote, remote_path),
            "--progress",
        ], timeout=300)

        if rc == 0:
            # Obtener URL real de Drive vía rclone link (más fiable que parsear copy)
            link_url = _fetch_drive_link(remote, remote_path)
            print(f"[Drive] ✅ Subido: {_remote_path(remote, remote_path)}")
            return {
                "ok": True,
                "remote_url": link_url,
                "remote_path": remote_path,
                "attempt": attempt,
            }

        last_err = stderr or stdout
        print(f"[Drive] ❌ Intento {attempt}/{RETRY_COUNT} falló: {last_err[:80]}")
        if attempt < RETRY_COUNT:
            time.sleep(RETRY_DELAY)

    return {
        "ok": False,
        "remote_url": None,
        "error": f"Falló después de {RETRY_COUNT} intentos: {last_err[:200]}",
    }


def _fetch_drive_link(remote: str, remote_path: str) -> Optional[str]:
    """Devuelve la URL compartible de Google Drive para un archivo subido.

    Reintenta un par de veces porque la API de Drive a veces tarda unos
    segundos en propagar el file_id tras el upload (consistencia eventual).
    """
    for attempt in range(1, 4):
        rc, out, err = _run_rclone(["link", _remote_path(remote, remote_path)], timeout=30)
        if rc == 0 and out.strip():
            return out.strip()
        if attempt < 3:
            time.sleep(2)
    return None


def setup_rclone_auth() -> None:
    """
    Inicia el flujo de autorización interactiva de rclone para Google Drive.
    Debe ejecutarse UNA SOLA VEZ desde terminal interactivo.
    Muestra una URL para abrir en el navegador.
    """
    print("=" * 60)
    print("SETUP RCLONE — Google Drive Authorization")
    print("=" * 60)
    print("\n1. Abri esta URL en tu navegador:")
    print("   rclone authorize drive \"NUNCA_USAR_ESTO\"\n")
    print("2. Ingresá con tu cuenta de Google.")
    print("3. Copiá el código de autorizacion y pegalo aca.\n")

    rc, stdout, stderr = _run_rclone([
        "authorize", "drive", "NUNCA_USAR_ESTO"
    ], timeout=300)

    if rc != 0:
        print(f"[ERROR] Falló la autorizacion: {stderr}")
        return

    # Extraer client_id y client_secret del JSON output
    try:
        auth_data = json.loads(stdout)
        client_id = auth_data.get("client_id", "")
        client_secret = auth_data.get("client_secret", "")
        if client_id and client_secret:
            print("\n✅ Autorización recibida.")
            print(f"   Client ID: {client_id[:20]}...")
            print(f"\nAhora hay que agregar esto a ~/.config/rclone/rclone.conf:\n")
            print(f"[drive]\n"
                  f"type = drive\n"
                  f"client_id = {client_id}\n"
                  f"client_secret = {client_secret}\n"
                  f"scope = drive\n"
                  f"token = {{\"access_token\":\"auth_token_here\"}}\n")
        else:
            print("[WARN] No se pudieron extraer credenciales del resultado.")
    except json.JSONDecodeError:
        print("[WARN] Salida no es JSON válido. Revisá manualmente:\n" + stdout[:300])
