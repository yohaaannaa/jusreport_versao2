import os
import sys
import time
import socket
import shutil
import subprocess
import webbrowser
from pathlib import Path

APP_NAME = "JusReport"

API_HOST = "127.0.0.1"
API_PORT = 8000
UI_PORT = 8501


# -------------------------
# Utilidades de porta
# -------------------------
def is_port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex((host, port)) == 0


def wait_until_listening(host: str, port: int, timeout_s: int = 40) -> bool:
    start = time.time()
    while time.time() - start < timeout_s:
        if is_port_in_use(host, port):
            return True
        time.sleep(0.3)
    return False


# -------------------------
# AppData (Windows)
# -------------------------
def appdata_dir() -> Path:
    base = os.environ.get("APPDATA")
    if not base:
        base = Path.home() / "AppData" / "Roaming"
    return Path(base) / APP_NAME


def ensure_appdata_structure() -> dict:
    base = appdata_dir()
    base.mkdir(parents=True, exist_ok=True)

    paths = {
        "base": base,
        "data": base / "data",
        "uploads": base / "uploads",
        "relatorios": base / "relatorios",
        "logs": base / "logs",
        "env": base / ".env",
        "env_example": base / ".env.example",
    }

    for key in ("data", "uploads", "relatorios", "logs"):
        paths[key].mkdir(parents=True, exist_ok=True)

    project_root = Path(__file__).resolve().parent
    src_env_example = project_root / ".env.example"

    if src_env_example.exists() and not paths["env_example"].exists():
        shutil.copyfile(src_env_example, paths["env_example"])

    if not paths["env"].exists():
        if paths["env_example"].exists():
            shutil.copyfile(paths["env_example"], paths["env"])
        else:
            paths["env"].write_text(
                "JUSREPORT_API_URL=http://127.0.0.1:8000\n"
                "MAX_PDF_CHARS=120000\n"
                "GEMINI_API_KEY=\n"
                "GEMINI_MODEL_TEXT=gemini-2.5-pro\n"
                "GEMINI_MODEL_OCR=gemini-2.5-pro\n",
                encoding="utf-8",
            )

    return paths


# -------------------------
# Main launcher
# -------------------------
def main():
    paths = ensure_appdata_structure()
    project_root = Path(__file__).resolve().parent

    os.chdir(project_root)

    env = os.environ.copy()
    env["JUSREPORT_APPDATA"] = str(paths["base"])
    env["JUSREPORT_ENV_PATH"] = str(paths["env"])
    env["JUSREPORT_DATA_DIR"] = str(paths["data"])
    env["JUSREPORT_UPLOADS_DIR"] = str(paths["uploads"])
    env["JUSREPORT_RELATORIOS_DIR"] = str(paths["relatorios"])
    env["JUSREPORT_LOG_DIR"] = str(paths["logs"])

    procs = []

    try:
        if not is_port_in_use(API_HOST, API_PORT):
            api_cmd = [
                sys.executable, "-m", "uvicorn",
                "app.api.main:app",
                "--host", API_HOST,
                "--port", str(API_PORT),
            ]
            procs.append(subprocess.Popen(api_cmd, env=env))

            if not wait_until_listening(API_HOST, API_PORT):
                raise RuntimeError("❌ API não iniciou (porta 8000).")

        if not is_port_in_use(API_HOST, UI_PORT):
            ui_cmd = [
                sys.executable, "-m", "streamlit", "run",
                "app/web/streamlit/ui.py",
                "--server.address", API_HOST,
                "--server.port", str(UI_PORT),
            ]
            procs.append(subprocess.Popen(ui_cmd, env=env))

            if not wait_until_listening(API_HOST, UI_PORT, timeout_s=60):
                raise RuntimeError("❌ Streamlit não iniciou (porta 8501).")

        webbrowser.open(f"http://{API_HOST}:{UI_PORT}")

        while True:
            if all(p.poll() is not None for p in procs):
                break
            time.sleep(0.5)

    finally:
        for p in procs:
            if p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass


if __name__ == "__main__":
    main()
