import os
import sys
import time
import traceback

# ================= AJUSTE DE PATH =================
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
# ==================================================

import base64
from io import BytesIO
from typing import Optional

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

from app.utils.db import (  # type: ignore
    salvar_processo,
    listar_processos,
    atualizar_status,
    registrar_relatorio,
    excluir_processo,
    DATA_DIR,
    REL_DIR,
)

# ========= CONFIGURAÇÕES =========
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

RELATORIOS_DIR = REL_DIR
API_BASE = os.getenv("JUSREPORT_API_URL", "http://127.0.0.1:8000")

SUMARIZACOES_DISPONIVEIS = [
    "Execução",
    "Ação de Cobrança",
    "Ação Monitória",
    "Embargos à Execução",
    "Reintegração de Posse",
]

os.makedirs(RELATORIOS_DIR, exist_ok=True)


# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

def _guess_mime(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return "application/pdf"
    if lower.endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return "application/octet-stream"


@st.cache_data(show_spinner=False)
def _carregar_logo_base64(logo_path: str) -> Optional[str]:
    if not os.path.exists(logo_path):
        return None
    with open(logo_path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def exibir_cabecalho() -> None:
    logo_path = os.path.join(os.path.dirname(__file__), "logo.png")
    encoded = _carregar_logo_base64(logo_path)
    if encoded:
        st.markdown(
            '<div style="display:flex;align-items:center;margin-bottom:8px;">'
            f'<img src="data:image/png;base64,{encoded}" style="width:55px;margin-right:20px;" />'
            '<h1 style="margin:0;font-size:36px;">JUSREPORT</h1>'
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        st.title("⚖️ JUSREPORT")


# ============================================================
# CHAMADAS À API
# ============================================================

def api_health() -> dict:
    try:
        r = requests.get(f"{API_BASE}/health", timeout=10)
        r.raise_for_status()
        data = r.json()
        data.setdefault("api_reachable", True)
        return data
    except Exception as e:
        return {
            "service": "jusreport-api",
            "api_reachable": False,
            "gemini_configured": False,
            "error": str(e),
        }


def api_ingest(file_path: str, case_number: str) -> dict:
    with open(file_path, "rb") as f:
        resp = requests.post(
            f"{API_BASE}/ingest",
            files=[("files", (os.path.basename(file_path), f, _guess_mime(file_path)))],
            data={"case_number": case_number},
            timeout=60,
        )
    resp.raise_for_status()
    return resp.json()


def api_status(job_id: str) -> dict:
    resp = requests.get(f"{API_BASE}/status/{job_id}", timeout=10)
    resp.raise_for_status()
    return resp.json()


def api_summarize(case_number: str, action_type: str) -> dict:
    query = (
        "Gerar relatório completo: Cabeçalho, Resumo da Inicial, "
        "Penhoras Online (RENAJUD/SISBAJUD/INFOJUD/SERASAJUD), "
        "Movimentações Processuais, Análise Jurídica completa."
    )
    resp = requests.post(
        f"{API_BASE}/summarize",
        json={
            "question": query,
            "case_number": case_number,
            "action_type": action_type,
            "k": 100,
            "return_json": True,
        },
        timeout=600,
    )
    resp.raise_for_status()
    return resp.json()


def api_export_docx(content_markdown: str, filename: str) -> bytes:
    resp = requests.post(
        f"{API_BASE}/export/docx",
        data={"content": content_markdown, "filename": filename},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.content


# ============================================================
# CAMADAS DE DADOS
# ============================================================

@st.cache_data(ttl=30, show_spinner=False)
def carregar_processos_pendentes_df() -> pd.DataFrame:
    rows = listar_processos(status="pendente")
    cols = ["id", "nome_cliente", "numero_processo", "tipo", "data_envio", "caminho_arquivo"]
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    for c in cols:
        if c not in df.columns:
            df[c] = None
    return df[cols].sort_values(by="data_envio", ascending=False)


@st.cache_data(ttl=30, show_spinner=False)
def carregar_processos_finalizados_df() -> pd.DataFrame:
    rows = listar_processos(status="finalizado")
    cols = ["id", "nome_cliente", "numero_processo", "data_envio", "caminho_relatorio"]
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    for c in cols:
        if c not in df.columns:
            df[c] = None
    return df[cols].sort_values(by="data_envio", ascending=False)


@st.cache_data(ttl=30, show_spinner=False)
def carregar_contagem_mensal_df() -> pd.DataFrame:
    rows = listar_processos(status=None)
    if not rows:
        return pd.DataFrame(columns=["nome_cliente", "mes_ano", "quantidade"])
    df = pd.DataFrame(rows)
    df["data_envio"] = pd.to_datetime(df["data_envio"], errors="coerce")
    df["mes_ano"] = df["data_envio"].dt.strftime("%m/%Y")
    return (
        df.groupby(["nome_cliente", "mes_ano"])
        .size()
        .reset_index(name="quantidade")
        .sort_values(by="mes_ano", ascending=False)
    )


def _invalidar_cache() -> None:
    carregar_processos_pendentes_df.clear()
    carregar_processos_finalizados_df.clear()
    carregar_contagem_mensal_df.clear()


def _excluir_com_arquivo(processo_id: str, caminho_arquivo: Optional[str]) -> None:
    excluir_processo(processo_id)
    if caminho_arquivo and os.path.exists(caminho_arquivo):
        try:
            os.remove(caminho_arquivo)
        except Exception:
            pass


# ============================================================
# APP STREAMLIT — INTERFACE INTERNA DO ESCRITÓRIO
# ============================================================

st.set_page_config(page_title="JusReport", page_icon="⚖️", layout="wide")

exibir_cabecalho()

health = api_health()
api_reachable = health.get("api_reachable", True)
gemini_ok = bool(health.get("gemini_configured"))

if not api_reachable:
    st.error(
        f"API indisponível em {API_BASE}. "
        f"Verifique JUSREPORT_API_URL no .env. Detalhe: {health.get('error')}"
    )
    st.stop()

if not gemini_ok:
    st.warning("⚠️ GEMINI_API_KEY não configurada. O processamento automático estará desativado.")

st.markdown("---")

# =====================================================================
# SEÇÃO 1 — ENVIAR NOVO PROCESSO
# =====================================================================
st.subheader("📂 Enviar Novo Processo")

with st.form("form_novo_processo"):
    col_a, col_b = st.columns(2)
    with col_a:
        nome_cliente = st.text_input("Colaborador")
        numero = st.text_input("Número do processo")
    with col_b:
        tipo = st.selectbox("Tipo de sumarização", SUMARIZACOES_DISPONIVEIS, index=0)
        arquivo = st.file_uploader("Arquivo do processo (PDF ou DOCX)", type=["pdf", "docx"])

    enviado = st.form_submit_button("➕ Cadastrar processo", use_container_width=True)

    if enviado:
        if not (nome_cliente and numero and arquivo):
            st.warning("Preencha todos os campos antes de enviar.")
        else:
            try:
                processo_id = salvar_processo(
                    nome_cliente=nome_cliente,
                    email="",
                    numero=numero,
                    tipo=tipo,
                    arquivo_bytes=arquivo.getvalue(),
                    nome_arquivo=arquivo.name,
                    conferencia="interno",
                )
                st.success("✅ Processo cadastrado com sucesso!")
                _invalidar_cache()
            except Exception as e:
                st.error(f"Erro ao cadastrar: {e}")
                with st.expander("📄 Detalhes técnicos"):
                    st.code("".join(traceback.format_exception(type(e), e, e.__traceback__)))

st.markdown("---")

# =====================================================================
# SEÇÃO 2 — PROCESSOS PENDENTES
# =====================================================================
st.subheader("⏳ Processos Pendentes")

df = carregar_processos_pendentes_df()

if df.empty:
    st.info("Nenhum processo pendente no momento.")
else:
    for _, row in df.iterrows():
        with st.container():
            try:
                data_fmt = pd.to_datetime(row["data_envio"]).strftime("%d/%m/%Y %H:%M")
            except Exception:
                data_fmt = row["data_envio"] or "—"

            st.markdown(
                f"**{row['nome_cliente']}** &nbsp;|&nbsp; "
                f"Processo: `{row['numero_processo']}` &nbsp;|&nbsp; "
                f"Tipo: {row['tipo']} &nbsp;|&nbsp; "
                f"Cadastrado em: {data_fmt}"
            )

            col1, col2, col3 = st.columns([2, 1, 1])

            with col1:
                caminho = row.get("caminho_arquivo")
                if caminho and os.path.exists(caminho):
                    with open(caminho, "rb") as f:
                        st.download_button(
                            label="⬇️ Baixar arquivo original",
                            data=f,
                            file_name=os.path.basename(caminho),
                            mime=_guess_mime(os.path.basename(caminho)),
                            key=f"dl_orig_{row['id']}",
                        )
                else:
                    st.warning("Arquivo original não encontrado.")

            with col2:
                if st.button("🗑️ Excluir", key=f"excluir_{row['id']}"):
                    try:
                        _excluir_com_arquivo(row["id"], row.get("caminho_arquivo"))
                        st.success("Processo excluído.")
                        _invalidar_cache()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erro ao excluir: {e}")

            with col3:
                if not gemini_ok:
                    st.button("🤖 Processar", key=f"processar_{row['id']}", disabled=True)
                    st.caption("Gemini não configurado.")
                elif st.button("🤖 Processar", key=f"processar_{row['id']}"):
                    caminho = row.get("caminho_arquivo")
                    if not caminho or not os.path.exists(caminho):
                        st.error("Arquivo não encontrado.")
                    else:
                        try:
                            with st.spinner("Elaborando Relatório..."):
                                # 1) Ingest
                                resp_ingest = api_ingest(
                                    file_path=caminho,
                                    case_number=str(row["numero_processo"]),
                                )
                                job_id = resp_ingest.get("job_id")
                                if not job_id:
                                    st.error(f"Falha ao iniciar ingestão: {resp_ingest}")
                                    st.stop()

                                # 2) Aguarda conclusão da ingestão silenciosamente
                                st_status = {}
                                while True:
                                    time.sleep(1.5)
                                    try:
                                        st_status = api_status(job_id)
                                    except Exception as poll_err:
                                        st.error(f"Erro ao consultar status: {poll_err}")
                                        st.stop()
                                    if st_status.get("status") in ("done", "error"):
                                        break

                                if st_status.get("status") != "done":
                                    st.error(f"Ingestão falhou: {st_status.get('detail')}")
                                    st.stop()

                                # 3) Sumarização
                                sum_resp = api_summarize(
                                    case_number=str(row["numero_processo"]),
                                    action_type=str(row["tipo"]),
                                )

                            summary_md = (sum_resp.get("summary_markdown") or "").strip()
                            if not summary_md:
                                st.error("A IA não retornou conteúdo.")
                                st.stop()

                            # 4) Export DOCX
                            nome_saida = f"JusReport_{row['numero_processo']}.docx"
                            docx_bytes = api_export_docx(
                                content_markdown=summary_md,
                                filename=nome_saida,
                            )

                            if not docx_bytes:
                                st.error("Falha ao gerar DOCX.")
                                st.stop()

                            # Persiste no disco e registra no banco
                            caminho_relatorio = os.path.join(RELATORIOS_DIR, nome_saida)
                            with open(caminho_relatorio, "wb") as out:
                                out.write(docx_bytes)

                            registrar_relatorio(row["id"], caminho_docx=caminho_relatorio)
                            _invalidar_cache()

                            st.success("✅ Relatório gerado com sucesso!")

                            # 📥 BOTÃO DE DOWNLOAD IMEDIATO
                            st.download_button(
                                label="📥 Baixar Relatório",
                                data=docx_bytes,
                                file_name=nome_saida,
                                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                key=f"dl_novo_{row['id']}",
                            )

                        except requests.HTTPError as e:
                            try:
                                st.error(f"Falha na API: {e.response.json()}")
                            except Exception:
                                st.error(f"Falha na API: {e}")
                            with st.expander("📄 Detalhes técnicos"):
                                st.code("".join(traceback.format_exception(type(e), e, e.__traceback__)))
                        except Exception as e:
                            st.error(f"Erro no processamento: {e}")
                            with st.expander("📄 Detalhes técnicos"):
                                st.code("".join(traceback.format_exception(type(e), e, e.__traceback__)))

            st.markdown("---")

# =====================================================================
# SEÇÃO 3 — RELATÓRIOS FINALIZADOS
# =====================================================================
st.subheader("✅ Relatórios Finalizados")

df_finalizados = carregar_processos_finalizados_df()

if df_finalizados.empty:
    st.info("Nenhum relatório finalizado ainda.")
else:
    try:
        df_finalizados["data_envio"] = pd.to_datetime(
            df_finalizados["data_envio"]
        ).dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        pass

    for _, row in df_finalizados.iterrows():
        col_info, col_dl = st.columns([4, 1])
        with col_info:
            st.markdown(
                f"**{row['nome_cliente']}** &nbsp;|&nbsp; "
                f"`{row['numero_processo']}` &nbsp;|&nbsp; "
                f"{row['data_envio']}"
            )
        with col_dl:
            caminho_rel = row.get("caminho_relatorio")
            if caminho_rel and os.path.exists(caminho_rel):
                with open(caminho_rel, "rb") as f:
                    st.download_button(
                        label="📥 Baixar",
                        data=f,
                        file_name=os.path.basename(caminho_rel),
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        key=f"dl_final_{row['id']}",
                    )
            else:
                st.caption("Arquivo não encontrado.")

    st.markdown("")
    output_excel = BytesIO()
    cols_excel = ["nome_cliente", "numero_processo", "data_envio"]
    with pd.ExcelWriter(output_excel, engine="openpyxl") as writer:
        df_finalizados[cols_excel].to_excel(writer, index=False, sheet_name="Finalizados")
    st.download_button(
        label="📊 Exportar lista em Excel",
        data=output_excel.getvalue(),
        file_name="relatorios_finalizados.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

st.markdown("---")

# =====================================================================
# SEÇÃO 4 — RELATÓRIO MENSAL
# =====================================================================
st.subheader("📅 Relatório Mensal")

df_mensal = carregar_contagem_mensal_df()

if df_mensal.empty:
    st.info("Nenhum processo cadastrado ainda.")
else:
    st.dataframe(df_mensal, use_container_width=True)
    output_mensal = BytesIO()
    with pd.ExcelWriter(output_mensal, engine="openpyxl") as writer:
        df_mensal.to_excel(writer, index=False, sheet_name="RelatorioMensal")
    st.download_button(
        label="📊 Exportar em Excel",
        data=output_mensal.getvalue(),
        file_name="relatorio_mensal.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
