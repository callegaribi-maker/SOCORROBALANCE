# -*- coding: utf-8 -*-
"""
App Streamlit — Equilíbrio estático: COP (plataforma de força) x COM (acelerômetro do celular)

Rodar localmente:   streamlit run app.py
"""
import io
import json

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

import balance_core as B

st.set_page_config(page_title="Equilíbrio COP x COM", page_icon="⚖️", layout="wide")

COR_COP = "#1f5a96"
COR_ACC = "#b5482b"
CONDICOES_PADRAO = ["OA", "OF", "JOA", "JOF", "TOA", "TOF"]


# =============================================================================
# Cache de leitura
# =============================================================================
@st.cache_data(show_spinner=False)
def carregar_plataforma(nome, conteudo, fs):
    bruto = B.ler_plataforma(io.BytesIO(conteudo))
    return bruto, B.reamostrar(bruto, fs)


@st.cache_data(show_spinner=False)
def carregar_celular(nome, conteudo, fs):
    bruto = B.ler_celular(io.BytesIO(conteudo))
    return bruto, B.reamostrar(bruto, fs)


def tipo_arquivo(conteudo):
    """'plataforma', 'celular' ou None, pelas colunas do cabeçalho.
    (cópia local: funciona mesmo se o balance_core.py do repositório for uma versão antiga)"""
    if hasattr(B, "tipo_arquivo"):
        return B.tipo_arquivo(conteudo)
    if isinstance(conteudo, bytes):
        conteudo = conteudo[:5000].decode("utf-8", errors="replace")
    cab = conteudo.splitlines()[0] if conteudo else ""
    cols = {c.strip().strip('"').lower() for c in cab.replace("\t", ",").replace(";", ",").split(",")}
    if {"fz", "copx", "copy"} <= cols:
        return "plataforma"
    if {"x", "y", "z"} <= cols:
        return "celular"
    return None


def chave_arquivos(arqs):
    return tuple((a.name, a.size) for a in arqs)


# =============================================================================
# Barra lateral: arquivos e parâmetros gerais
# =============================================================================
st.sidebar.title("⚖️ Equilíbrio COP x COM")
sujeito = st.sidebar.text_input("Participante", value="Participante")

arq_plat = st.sidebar.file_uploader(
    "Plataforma de força (Time, Fz, COPx, COPy)", type=["txt", "csv", "tsv"], accept_multiple_files=True,
    help="Protocolo real: 1 arquivo com o salto + todos os registros. "
         "Também aceita 1 arquivo por condição (o que tiver o salto sincroniza pelo salto; "
         "os outros por correlação).")
arq_cel = st.sidebar.file_uploader("Celular / acelerômetro (TempoMs, X, Y, Z)", type=["txt", "csv"])

with st.sidebar.expander("Parâmetros gerais", expanded=False):
    fs = st.number_input("Frequência de reamostragem (Hz)", 50.0, 1000.0, 100.0, 10.0)
    unidade_cop = st.selectbox("Unidade do COP", ["cm", "mm", "m"], index=0)
    st.markdown("**Eixos do celular**")
    c1, c2 = st.columns(2)
    eixo_v = c1.selectbox("Vertical", ["X", "Y", "Z"], index=1)
    eixo_ap = c1.selectbox("AP", ["X", "Y", "Z"], index=2)
    eixo_ml = c1.selectbox("ML", ["X", "Y", "Z"], index=0)
    sinal_ap = c2.selectbox("Sinal AP", [+1, -1], index=0)
    sinal_ml = c2.selectbox("Sinal ML", [+1, -1], index=1)
    st.markdown("**Busca do salto (s desde o início)**")
    c1, c2 = st.columns(2)
    jan_plat = c1.slider("Plataforma", 0, 120, (0, 30))
    jan_cel = c2.slider("Celular", 0, 180, (0, 60))

eixos = dict(vertical=eixo_v, ap=eixo_ap, ml=eixo_ml, sinal_ap=sinal_ap, sinal_ml=sinal_ml)

st.title("Equilíbrio estático — COP (plataforma) x COM (celular)")

# --- reconhece cada arquivo pelas colunas (corrige arquivos carregados no campo trocado) ---
todos = list(arq_plat or []) + ([arq_cel] if arq_cel is not None else [])
vistos, tipos = set(), {"plataforma": [], "celular": [], None: []}
for a in todos:
    if (a.name, a.size) in vistos:
        continue
    vistos.add((a.name, a.size))
    tipos[tipo_arquivo(a.getvalue())].append(a)
for a in tipos[None]:
    st.sidebar.error(f"'{a.name}': colunas não reconhecidas. Plataforma precisa de Fz, COPx, COPy; "
                     "celular precisa de X, Y, Z.")
trocado = any(tipo_arquivo(a.getvalue()) == "celular" for a in (arq_plat or [])) or \
    (arq_cel is not None and tipo_arquivo(arq_cel.getvalue()) == "plataforma")
if todos and trocado:
    st.sidebar.warning("Alguns arquivos estavam no campo trocado — reconhecidos pelas colunas e "
                       "usados corretamente.")
if len(tipos["celular"]) > 1:
    st.sidebar.error("Mais de um arquivo de celular carregado: deixe só um.")
    st.stop()
arq_plat = tipos["plataforma"]
arq_cel = tipos["celular"][0] if tipos["celular"] else None

if not arq_plat or arq_cel is None:
    falta = [n for n, ok in [("plataforma (Fz, COPx, COPy)", arq_plat), ("celular (X, Y, Z)", arq_cel)] if not ok]
    if todos:
        st.warning("Falta o arquivo: " + " e ".join(falta))
    st.info("⬅️ Carregue o(s) arquivo(s) da plataforma e o arquivo do celular na barra lateral.")
    st.markdown(
        """
**Fluxo do app**
1. **Sincronização**: pico do salto (Fz da plataforma × eixo vertical do celular), com ajuste manual.
2. **Segmentação**: janelas de cada condição detectadas automaticamente; você edita condição, início, fim e rotação na tabela.
3. **Filtragem**: escolha detrend, passa-baixa, passa-alta e ordem, separadamente para COP e celular.
4. **Gráficos e variáveis**: AP × tempo, ML × tempo, AP × ML com elipse 95%, tabela de variáveis e Excel para download.
"""
    )
    st.stop()

arq_plat = sorted(arq_plat, key=lambda a: a.name)
with st.spinner("Lendo arquivos..."):
    try:
        plats = {a.name: carregar_plataforma(a.name, a.getvalue(), fs) for a in arq_plat}
        cel_bruto, cel = carregar_celular(arq_cel.name, arq_cel.getvalue(), fs)
    except Exception as e:  # noqa: BLE001
        st.error(f"Não consegui ler os arquivos: {type(e).__name__}: {e}")
        st.stop()

modo_unico = len(plats) == 1
st.sidebar.success(
    f"{len(plats)} arquivo(s) de plataforma · celular {cel.t.iloc[-1] - cel.t.iloc[0]:.0f} s "
    f"(~{B.fs_original(cel_bruto):.0f} Hz)")

# reinicia estado quando os arquivos mudam
chave = (chave_arquivos(arq_plat), arq_cel.name, arq_cel.size, fs, json.dumps(eixos), jan_plat, jan_cel)
if st.session_state.get("chave") != chave:
    st.session_state.chave = chave
    for k in ["sinc", "seg", "seg_editor"]:
        st.session_state.pop(k, None)

# =============================================================================
# 1. SINCRONIZAÇÃO
# =============================================================================
st.header("1. Sincronização pelo salto")

t_salto_cel = B.pico_salto(cel, eixo_v, jan_cel)

if "sinc" not in st.session_state:
    sinc = {}
    for nome, (bruto, p) in plats.items():
        peso = B.peso_corporal(p)
        if modo_unico or B.tem_salto(p, peso):
            tp = B.pico_salto(p, "Fz", jan_plat)
            sinc[nome] = dict(metodo="salto", t_salto_plat=tp, offset=t_salto_cel - tp, peso=peso,
                              r_AP=np.nan, r_ML=np.nan)
        else:
            sinc[nome] = dict(metodo="correlação", t_salto_plat=np.nan, offset=np.nan, peso=peso,
                              r_AP=np.nan, r_ML=np.nan)
    # arquivos sem salto: correlação, procurando depois do arquivo anterior
    t_min = cel.t.iloc[0]
    for nome, (bruto, p) in plats.items():
        s = sinc[nome]
        if s["metodo"] == "correlação":
            a, b = B.janela_estavel(p, s["peso"], fs, 30.0)
            seg = p[(p.t >= a) & (p.t < b)].reset_index(drop=True)
            s["offset"], s["r_AP"], s["r_ML"] = B.offset_por_correlacao(seg, cel, fs, eixos, t_min)
        if not np.isnan(s["offset"]):
            t_min = p.t.iloc[-1] + s["offset"]
    st.session_state.sinc = sinc

sinc = st.session_state.sinc

tab_sinc = pd.DataFrame([dict(Arquivo=n, Metodo=s["metodo"], Pico_salto_plat_s=s["t_salto_plat"],
                              Pico_salto_cel_s=t_salto_cel if s["metodo"] == "salto" else np.nan,
                              Offset_s=s["offset"], r_AP=s["r_AP"], r_ML=s["r_ML"],
                              Peso_N=s["peso"]) for n, s in sinc.items()])

col_g, col_t = st.columns([2, 1])
with col_t:
    st.markdown("**Offset (t_celular − t_plataforma)** — edite para ajuste fino")
    novo = {}
    for nome, s in sinc.items():
        novo[nome] = st.number_input(f"{nome}", value=float(s["offset"]) if not np.isnan(s["offset"]) else 0.0,
                                     step=0.01, format="%.3f", key=f"off_{nome}")
    for nome in sinc:
        sinc[nome]["offset"] = novo[nome]
    st.dataframe(tab_sinc.drop(columns=["Offset_s"]).round(3), hide_index=True, width="stretch")
    if st.button("↺ Redetectar sincronização"):
        st.session_state.pop("sinc", None)
        for nome in sinc:
            st.session_state.pop(f"off_{nome}", None)
        st.session_state.pop("seg", None)
        st.rerun()

with col_g:
    nome_salto = next((n for n, s in sinc.items() if s["metodo"] == "salto"), None)
    if nome_salto:
        p = plats[nome_salto][1]
        tp = sinc[nome_salto]["t_salto_plat"]
        off = sinc[nome_salto]["offset"]
        mp = (p.t > tp - 3) & (p.t < tp + 3)
        mc = (cel.t > tp + off - 3) & (cel.t < tp + off + 3)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=p.t[mp] - tp, y=p.Fz[mp] / p.Fz[mp].max(), name="Fz plataforma",
                                 line=dict(color=COR_COP)))
        fig.add_trace(go.Scatter(x=cel.t[mc] - (tp + off), y=cel[eixo_v][mc] / cel[eixo_v][mc].max(),
                                 name=f"{eixo_v} celular", line=dict(color=COR_ACC)))
        fig.add_vline(x=0, line_dash="dash", line_color="gray")
        fig.update_layout(title=f"Salto alinhado ({nome_salto}) — offset {off:.3f} s",
                          xaxis_title="tempo relativo ao pico do salto (s)", yaxis_title="normalizado",
                          height=330, margin=dict(t=50, b=40), legend=dict(orientation="h", y=-0.25))
        st.plotly_chart(fig, width="stretch")
    else:
        st.warning("Nenhum arquivo da plataforma com salto: todos foram sincronizados por correlação.")

    # registro inteiro sobreposto
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.06,
                        subplot_titles=("Fz da plataforma (no tempo do celular)", f"{eixo_v} do celular"))
    for nome, (bruto, p) in plats.items():
        off = sinc[nome]["offset"]
        fig.add_trace(go.Scattergl(x=p.t + off, y=p.Fz, name=nome, line=dict(width=1)), 1, 1)
    fig.add_trace(go.Scattergl(x=cel.t, y=cel[eixo_v], name="celular", line=dict(color=COR_ACC, width=0.7)), 2, 1)
    fig.update_layout(height=420, margin=dict(t=40, b=30), showlegend=not modo_unico)
    fig.update_xaxes(title_text="tempo do celular (s)", row=2, col=1)
    st.plotly_chart(fig, width="stretch")

# =============================================================================
# 2. SEGMENTAÇÃO
# =============================================================================
st.header("2. Segmentação das condições")

c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
duracao = c1.number_input("Duração analisada (s)", 5.0, 120.0, 30.0, 1.0,
                          help="Últimos N s estáveis de cada registro (corta subida/descida da plataforma).")
metodo_seg = c2.selectbox("Detecção", ["Automática (blocos em pé)", "Agenda (40 s + 3 s)"],
                          disabled=not modo_unico)
ordem_txt = c4.text_input("Ordem das condições deste participante",
                          value=" ".join(CONDICOES_PADRAO if modo_unico else
                                         [n.rsplit(".", 1)[0][-3:].upper() for n in plats]),
                          help="Separadas por espaço, na ordem em que foram coletadas.")
ordem = [o.strip().upper() for o in ordem_txt.replace(",", " ").split() if o.strip()]
if metodo_seg.startswith("Agenda"):
    c3a, c3b = c3.columns(2)
    dur_reg = c3a.number_input("Registro (s)", 5.0, 120.0, 40.0)
    intervalo = c3b.number_input("Intervalo (s)", 0.0, 60.0, 3.0)


def segmentacao_inicial():
    linhas = []
    if modo_unico:
        nome = next(iter(plats))
        p = plats[nome][1]
        s = sinc[nome]
        if metodo_seg.startswith("Agenda"):
            t_ini = B.inicio_estavel(p[p.t > s["t_salto_plat"] + 0.5], s["peso"], fs=fs)
            jan = B.segmentar_agenda(t_ini, len(ordem), dur_reg, intervalo, duracao)
        else:
            jan = B.segmentar_auto(p, s["peso"], fs, s["t_salto_plat"], duracao)
        if len(jan) != len(ordem):
            st.warning(f"Foram detectados {len(jan)} blocos e a ordem tem {len(ordem)} condições. "
                       "Confira e edite a tabela.")
        for k, (a, b) in enumerate(jan):
            linhas.append(dict(Condicao=ordem[k] if k < len(ordem) else f"C{k + 1}", Arquivo=nome,
                               Inicio_s=round(a, 2), Fim_s=round(b, 2), Girar_90=False, Incluir=True))
    else:
        for k, (nome, (bruto, p)) in enumerate(plats.items()):
            a, b = B.janela_estavel(p, sinc[nome]["peso"], fs, duracao)
            linhas.append(dict(Condicao=ordem[k] if k < len(ordem) else f"C{k + 1}", Arquivo=nome,
                               Inicio_s=round(a, 2), Fim_s=round(b, 2), Girar_90=False, Incluir=True))
    return pd.DataFrame(linhas)


chave_seg = (duracao, metodo_seg, tuple(ordem),
             (dur_reg, intervalo) if metodo_seg.startswith("Agenda") else None)
if st.session_state.get("seg_chave") != chave_seg or "seg" not in st.session_state:
    st.session_state.seg = segmentacao_inicial()
    st.session_state.seg_chave = chave_seg
    st.session_state.pop("seg_editor", None)

if st.button("↺ Redetectar segmentação"):
    st.session_state.seg = segmentacao_inicial()
    st.session_state.pop("seg_editor", None)
    st.rerun()

# figura e ajuste fino aparecem ANTES da tabela (preenchidos depois, com os valores editados)
area_fig = st.container()
area_fino = st.container()

st.caption("Edite livremente: nome da condição, início/fim (s, no tempo da plataforma), "
           "'Girar_90' para quem ficou de lado na plataforma (AP = COPx, ML = −COPy), "
           "ou adicione/remova linhas.")
seg_full = st.data_editor(
    st.session_state.seg, key="seg_editor", num_rows="dynamic", width="stretch", hide_index=True,
    column_config=dict(
        Condicao=st.column_config.TextColumn("Condição", required=True),
        Arquivo=st.column_config.SelectboxColumn("Arquivo", options=list(plats), required=True),
        Inicio_s=st.column_config.NumberColumn("Início (s)", step=0.01, format="%.2f"),
        Fim_s=st.column_config.NumberColumn("Fim (s)", step=0.01, format="%.2f"),
        Girar_90=st.column_config.CheckboxColumn("Girar 90°"),
        Incluir=st.column_config.CheckboxColumn("Incluir"),
    ))
seg = seg_full.dropna(subset=["Condicao", "Arquivo", "Inicio_s", "Fim_s"])
seg = seg[seg.Incluir.fillna(True).astype(bool)]

# ajuste fino por slider
with area_fino.expander("Ajuste fino de uma janela com slider"):
    if len(seg):
        idx = st.selectbox("Condição", seg.index, format_func=lambda i: seg.loc[i, "Condicao"])
        p = plats[seg.loc[idx, "Arquivo"]][1]
        a, b = st.slider("Início / fim (s)", float(p.t.iloc[0]), float(p.t.iloc[-1]),
                         (float(seg.loc[idx, "Inicio_s"]), float(seg.loc[idx, "Fim_s"])), 0.1,
                         key=f"sl_{idx}")
        if st.button("Aplicar à tabela"):
            df = seg_full.copy()
            df.loc[idx, ["Inicio_s", "Fim_s"]] = [round(a, 2), round(b, 2)]
            st.session_state.seg = df
            st.session_state.pop("seg_editor", None)
            st.rerun()

# gráfico de segmentação
cores = ["#4C78A8", "#F58518", "#54A24B", "#E45756", "#B279A2", "#9D755D", "#72B7B2", "#EECA3B"]
for nome, (bruto, p) in plats.items():
    sub = seg[seg.Arquivo == nome]
    off = sinc[nome]["offset"]
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.04,
                        subplot_titles=("", f"COP (centrado, {unidade_cop})",
                                        "Celular — AP e ML (m/s², centrado)"))
    fig.add_trace(go.Scattergl(x=p.t, y=p.Fz, line=dict(color="black", width=0.8), name="Fz"), 1, 1)
    fig.add_trace(go.Scattergl(x=p.t, y=p.COPx - p.COPx.median(), name="COPx (ML)",
                               line=dict(width=0.8, color="#1f77b4")), 2, 1)
    fig.add_trace(go.Scattergl(x=p.t, y=p.COPy - p.COPy.median(), name="COPy (AP)",
                               line=dict(width=0.8, color="#d62728")), 2, 1)
    m = (cel.t >= p.t.iloc[0] + off) & (cel.t <= p.t.iloc[-1] + off)
    aap, aml = B.acc_ap_ml(cel[m], eixos)
    fig.add_trace(go.Scattergl(x=cel.t[m] - off, y=aap - np.median(aap), name="ACC AP",
                               line=dict(width=0.7, color="#d62728")), 3, 1)
    fig.add_trace(go.Scattergl(x=cel.t[m] - off, y=aml - np.median(aml), name="ACC ML",
                               line=dict(width=0.7, color="#1f77b4")), 3, 1)
    for k, (_, r) in enumerate(sub.iterrows()):
        for row in (1, 2, 3):
            fig.add_vrect(x0=r.Inicio_s, x1=r.Fim_s, fillcolor=cores[k % len(cores)], opacity=0.2,
                          line_width=0, row=row, col=1)
        fig.add_annotation(x=(r.Inicio_s + r.Fim_s) / 2, y=0.97, xref="x", yref="y domain",
                           text=f"<b>{r.Condicao}</b>", showarrow=False, yanchor="top", row=1, col=1)
    # escala do COP pelos trechos em pé (Fz > 50% do peso): ignora o ruído com a plataforma vazia
    em_pe = p.Fz > 0.5 * sinc[nome]["peso"]
    vals = np.concatenate([(p.COPx - p.COPx.median())[em_pe], (p.COPy - p.COPy.median())[em_pe]])
    lim = 1.2 * np.nanpercentile(np.abs(vals), 99.5) if len(vals) else 5
    fig.update_yaxes(range=[-max(lim, 1), max(lim, 1)], row=2, col=1)
    fig.update_layout(height=680, margin=dict(t=70, b=70), title=nome if not modo_unico else None,
                      legend=dict(orientation="h", y=1.02, yanchor="bottom", x=1, xanchor="right"))
    fig.update_xaxes(title_text="tempo da plataforma (s)", row=3, col=1)
    fig.update_yaxes(title_text="Fz (N)", row=1, col=1)
    area_fig.plotly_chart(fig, width="stretch")

# =============================================================================
# 3. FILTRAGEM
# =============================================================================
st.header("3. Filtragem")


def caixa_filtro(titulo, chave, padrao_pb=6.0):
    with st.container(border=True):
        st.markdown(f"**{titulo}**")
        c1, c2, c3, c4 = st.columns(4)
        det = c1.checkbox("Remover tendência (detrend)", True, key=f"det_{chave}")
        usa_pb = c2.checkbox("Passa-baixa", True, key=f"upb_{chave}")
        pb = c2.number_input("Corte passa-baixa (Hz)", 0.1, fs / 2 - 0.1, padrao_pb, 0.5, key=f"pb_{chave}",
                             disabled=not usa_pb)
        usa_pa = c3.checkbox("Passa-alta", False, key=f"upa_{chave}")
        pa = c3.number_input("Corte passa-alta (Hz)", 0.01, 10.0, 0.1, 0.05, key=f"pa_{chave}", disabled=not usa_pa)
        od = c4.selectbox("Ordem Butterworth", [2, 4, 6, 8], index=1, key=f"od_{chave}",
                          help="filtfilt (fase zero) — a ordem efetiva dobra.")
    return dict(detrend_on=det, passa_baixa=pb if usa_pb else None, passa_alta=pa if usa_pa else None, ordem=od)


mesmo = st.checkbox("Usar o mesmo filtro para COP e celular", True)
filtro_cop = caixa_filtro("Filtro — COP (plataforma)" if not mesmo else "Filtro — COP e celular", "cop")
filtro_acc = filtro_cop if mesmo else caixa_filtro("Filtro — celular (acelerômetro)", "acc")

# =============================================================================
# 4. PROCESSAMENTO, GRÁFICOS E VARIÁVEIS
# =============================================================================
st.header("4. Gráficos e variáveis")

resultados, erros = {}, []
for _, r in seg.iterrows():
    p = plats[r.Arquivo][1]
    try:
        resultados[r.Condicao] = B.processar_condicao(
            p, cel, float(r.Inicio_s), float(r.Fim_s), sinc[r.Arquivo]["offset"], fs, eixos,
            filtro_cop, filtro_acc, bool(r.Girar_90))
        resultados[r.Condicao]["linha"] = r
    except Exception as e:  # noqa: BLE001
        erros.append(f"{r.Condicao}: {e}")
for e in erros:
    st.error(e)
if not resultados:
    st.stop()

for cond, res in resultados.items():
    if B.suspeita_rotacao(res["concordancia"]) and not bool(res["linha"].Girar_90):
        st.warning(f"**{cond}**: os eixos AP/ML do COP parecem girados 90° em relação ao celular "
                   f"(r COP-ML×ACC-AP = {res['concordancia']['r_COPML_ACCAP']:.2f}). "
                   "Se a pessoa ficou de lado na plataforma, marque 'Girar 90°' na tabela.")
    if res["fz_min"] < 0.85 * sinc[res['linha'].Arquivo]["peso"]:
        st.warning(f"**{cond}**: a janela inclui Fz baixa ({res['fz_min']:.0f} N) — "
                   "provável subida/descida da plataforma. Ajuste início/fim.")


def fig_condicao(cond, res):
    fig = make_subplots(rows=2, cols=3, column_widths=[0.38, 0.38, 0.24], horizontal_spacing=0.07,
                        vertical_spacing=0.14,
                        subplot_titles=("COP — AP × tempo", "COP — ML × tempo", "COP — AP × ML",
                                        "Celular — AP × tempo", "Celular — ML × tempo", "Celular — AP × ML"))
    t = res["tempo"]
    for row, (ap, ml, cor, un) in enumerate([(res["cop_ap"], res["cop_ml"], COR_COP, unidade_cop),
                                              (res["acc_ap"], res["acc_ml"], COR_ACC, "m/s²")], start=1):
        fig.add_trace(go.Scatter(x=t, y=ap, line=dict(color=cor, width=1), showlegend=False), row, 1)
        fig.add_trace(go.Scatter(x=t, y=ml, line=dict(color=cor, width=1), showlegend=False), row, 2)
        fig.add_trace(go.Scatter(x=ml, y=ap, line=dict(color=cor, width=0.8), showlegend=False), row, 3)
        ex, ey = B.contorno_elipse(ap, ml)
        fig.add_trace(go.Scatter(x=ex, y=ey, fill="toself", fillcolor=cor, opacity=0.18,
                                 line=dict(color=cor, width=1), showlegend=False, hoverinfo="skip"), row, 3)
        lim = 1.15 * max(np.abs(ap).max(), np.abs(ml).max())
        fig.update_xaxes(range=[-lim, lim], title_text=f"ML ({un})", row=row, col=3)
        fig.update_yaxes(range=[-lim, lim], title_text=f"AP ({un})", scaleanchor=f"x{(row - 1) * 3 + 3}",
                         row=row, col=3)
        fig.update_xaxes(title_text="tempo (s)", row=row, col=1)
        fig.update_xaxes(title_text="tempo (s)", row=row, col=2)
        fig.update_yaxes(title_text=f"AP ({un})", row=row, col=1)
        fig.update_yaxes(title_text=f"ML ({un})", row=row, col=2)
    fig.update_layout(height=640, margin=dict(t=50, b=40), title=f"Condição {cond}")
    return fig


abas = st.tabs(list(resultados) + ["📊 Comparação"])
for aba, (cond, res) in zip(abas, resultados.items()):
    with aba:
        st.plotly_chart(fig_condicao(cond, res), width="stretch")
        r = res["linha"]
        c = res["concordancia"]
        st.caption(f"Janela plataforma {r.Inicio_s:.2f}–{r.Fim_s:.2f} s · celular "
                   f"{r.Inicio_s + sinc[r.Arquivo]['offset']:.2f} s · {len(res['tempo']) / fs:.1f} s · "
                   f"r(AP) COP×ACC = {c['r_AP_AP']:.2f}, r(ML) = {c['r_ML_ML']:.2f}"
                   + (" · eixos do COP girados 90°" if bool(r.Girar_90) else ""))
        v = pd.DataFrame({f"COP ({unidade_cop})": res["var_cop"], "Celular (m/s²)": res["var_acc"]})
        st.dataframe(v.style.format("{:.4g}"), width="stretch")

# tabela geral
linhas = []
for cond, res in resultados.items():
    r = res["linha"]
    for fonte, v, un in [("COP", res["var_cop"], unidade_cop), ("ACC_celular", res["var_acc"], "m/s²")]:
        linhas.append(dict(Sujeito=sujeito, Condicao=cond, Fonte=fonte, Unidade=un, Arquivo=r.Arquivo,
                           Inicio_plat_s=r.Inicio_s, Fim_plat_s=r.Fim_s,
                           Inicio_cel_s=r.Inicio_s + sinc[r.Arquivo]["offset"], Girar_90=bool(r.Girar_90), **v))
tab = pd.DataFrame(linhas)

with abas[-1]:
    nomes_var = [c for c in tab.columns if c not in
                 ("Sujeito", "Condicao", "Fonte", "Unidade", "Arquivo", "Inicio_plat_s", "Fim_plat_s",
                  "Inicio_cel_s", "Girar_90", "Duracao_s")]
    var = st.selectbox("Variável", nomes_var, index=nomes_var.index("Area_elipse95"))
    c1, c2 = st.columns(2)
    for col, fonte, cor in [(c1, "COP", COR_COP), (c2, "ACC_celular", COR_ACC)]:
        d = tab[tab.Fonte == fonte]
        fig = go.Figure(go.Bar(x=d.Condicao, y=d[var], marker_color=cor))
        fig.update_layout(title=f"{var} — {fonte}", height=360, margin=dict(t=50, b=30))
        col.plotly_chart(fig, width="stretch")
    st.dataframe(tab, width="stretch", hide_index=True)

# =============================================================================
# Download
# =============================================================================
st.header("5. Exportar")


def gerar_excel():
    largo = tab.drop(columns=["Unidade", "Arquivo", "Inicio_plat_s", "Fim_plat_s", "Inicio_cel_s",
                              "Girar_90"]).melt(id_vars=["Sujeito", "Condicao", "Fonte"], var_name="Variavel")
    largo["col"] = largo.Fonte + "_" + largo.Variavel + "_" + largo.Condicao
    uma = largo.pivot_table(index="Sujeito", columns="col", values="value", sort=False).reset_index()
    conc = pd.DataFrame([dict(Condicao=c, **r["concordancia"]) for c, r in resultados.items()])
    params = dict(fs=fs, unidade_cop=unidade_cop, eixos=eixos, filtro_cop=filtro_cop, filtro_acc=filtro_acc,
                  janela_salto_plat=jan_plat, janela_salto_cel=jan_cel, duracao=duracao,
                  arquivo_celular=arq_cel.name)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        tab.to_excel(w, sheet_name="Variaveis", index=False)
        uma.to_excel(w, sheet_name="Linha_unica_sujeito", index=False)
        tab_sinc.assign(Offset_s=[sinc[n]["offset"] for n in tab_sinc.Arquivo]).to_excel(
            w, sheet_name="Sincronizacao", index=False)
        seg.to_excel(w, sheet_name="Segmentacao", index=False)
        conc.round(3).to_excel(w, sheet_name="Concordancia_eixos", index=False)
        pd.Series({k: json.dumps(v) if isinstance(v, (dict, tuple, list)) else v
                   for k, v in params.items()}).to_frame("valor").to_excel(w, sheet_name="Parametros")
        for cond, res in resultados.items():
            pd.DataFrame(dict(tempo_s=res["tempo"], COP_AP=res["cop_ap"], COP_ML=res["cop_ml"],
                              ACC_AP=res["acc_ap"], ACC_ML=res["acc_ml"])).to_excel(
                w, sheet_name=f"Sinal_{cond}"[:31], index=False)
    return buf.getvalue()


c1, c2 = st.columns(2)
c1.download_button("⬇️ Baixar Excel com resultados", gerar_excel(),
                   file_name=f"{sujeito}_resultados.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                   type="primary")
c2.download_button("⬇️ Baixar segmentação (CSV)", seg.to_csv(index=False).encode("utf-8"),
                   file_name=f"{sujeito}_segmentacao.csv", mime="text/csv")
st.caption("Dica: cada gráfico tem um ícone de câmera (canto superior direito) para salvar como PNG.")
