import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True, parents=True)

DB_PATH = DATA_DIR / "banco_dados.db"
REL_DIR = DATA_DIR / "relatorios"
REL_DIR.mkdir(exist_ok=True, parents=True)

UPLOADS_DIR = DATA_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True, parents=True)


# ============================================================
# CONEXÃO COM CONTEXT MANAGER (fecha automaticamente)
# ============================================================

@contextmanager
def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ============================================================
# INICIALIZAÇÃO DO BANCO
# ============================================================

def _init_db():
    with _get_conn() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS processos (
            id TEXT PRIMARY KEY,
            nome_cliente TEXT,
            email TEXT,
            numero_processo TEXT,
            tipo TEXT,
            conferencia TEXT,
            data_envio TEXT,
            caminho_arquivo TEXT,
            status TEXT,
            caminho_relatorio TEXT
        )
        """)

_init_db()


# ============================================================
# OPERAÇÕES DE DADOS
# ============================================================

def salvar_processo(
    nome_cliente: str,
    email: str,
    numero: str,
    tipo: str,
    arquivo_bytes: bytes,
    nome_arquivo: str,
    conferencia: str,
) -> str:
    """
    Salva o processo no disco e registra no banco como 'pendente'.

    Recebe bytes diretamente em vez de um objeto UploadedFile do Streamlit,
    tornando esta função reutilizável fora do contexto da UI.
    """
    from uuid import uuid4
    proc_id = str(uuid4())

    ext = os.path.splitext(nome_arquivo)[1]
    file_path = UPLOADS_DIR / f"{proc_id}{ext}"

    file_path.write_bytes(arquivo_bytes)

    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO processos
            (id, nome_cliente, email, numero_processo, tipo, conferencia,
             data_envio, caminho_arquivo, status, caminho_relatorio)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                proc_id,
                nome_cliente,
                email,
                numero,
                tipo,
                conferencia,
                datetime.now().isoformat(),
                str(file_path),
                "pendente",
                None,
            ),
        )
    return proc_id


def listar_processos(status: Optional[str] = None) -> List[Dict[str, Any]]:
    with _get_conn() as conn:
        if status:
            cur = conn.execute(
                "SELECT * FROM processos WHERE status = ? ORDER BY data_envio DESC",
                (status,),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM processos ORDER BY data_envio DESC"
            )
        return [dict(r) for r in cur.fetchall()]


def buscar_processo(proc_id: str) -> Optional[Dict[str, Any]]:
    """Retorna um processo pelo ID ou None se não encontrado."""
    with _get_conn() as conn:
        cur = conn.execute("SELECT * FROM processos WHERE id = ?", (proc_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def atualizar_status(proc_id: str, novo_status: str) -> None:
    with _get_conn() as conn:
        conn.execute(
            "UPDATE processos SET status = ? WHERE id = ?",
            (novo_status, proc_id),
        )


def registrar_relatorio(proc_id: str, caminho_docx: str) -> None:
    with _get_conn() as conn:
        conn.execute(
            "UPDATE processos SET caminho_relatorio = ?, status = ? WHERE id = ?",
            (caminho_docx, "finalizado", proc_id),
        )


def excluir_processo(proc_id: str) -> Optional[str]:
    """
    Remove o processo do banco e retorna o caminho do arquivo
    para que o chamador possa apagar do disco, se desejar.
    """
    with _get_conn() as conn:
        cur = conn.execute(
            "SELECT caminho_arquivo FROM processos WHERE id = ?", (proc_id,)
        )
        row = cur.fetchone()
        if not row:
            return None
        caminho = row["caminho_arquivo"]
        conn.execute("DELETE FROM processos WHERE id = ?", (proc_id,))
    return caminho
