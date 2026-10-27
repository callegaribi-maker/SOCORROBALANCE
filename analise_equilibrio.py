# -*- coding: utf-8 -*-
"""
ANÁLISE DE EQUILÍBRIO ESTÁTICO — PLATAFORMA DE FORÇA (COP) + CELULAR (COM / acelerometria)
=========================================================================================

O que faz
---------
1. Importa o arquivo da plataforma (Time, Fz, COPx, COPy) e o do celular (TempoMs, X, Y, Z).
2. Sincroniza pelo SALTO inicial: pico de Fz da plataforma (impacto da aterrissagem)
   x pico do eixo Y (vertical) do celular.
3. Separa os registros de cada condição (OA, OF, JOA, JOF, TOA, TOF ...) na ordem
   informada para aquele participante.
4. Calcula as variáveis de equilíbrio (Farias et al., Lepr Rev 2026; Quijoux et al., 2021)
   para o COP e para a aceleração do tronco (COM):
       RMS AP/ML, amplitude AP/ML, deslocamento total (path), velocidade média,
       área da elipse 95%, frequência mediana AP/ML,
       energia em baixa (0–0,5 Hz), média (0,5–2 Hz) e alta (2–8 Hz) frequência AP/ML.
5. Gera gráficos AP x tempo, ML x tempo e AP x ML (estatocinesigrama com elipse 95%).
6. Salva tudo num Excel + PNGs.

Dois modos
----------
* MODO "unico"   (protocolo real): 1 arquivo da plataforma com o salto + os 6 registros.
    Os registros são encontrados automaticamente (blocos em que a pessoa está sobre a
    plataforma), ou por agenda fixa (duração 40 s + intervalo 3 s), ou por tempos manuais.
* MODO "separado" (arquivos de teste): 1 arquivo da plataforma por condição.
    O arquivo que contém o salto é sincronizado pelo salto; os demais por correlação
    cruzada entre COP e aceleração do celular (procurando sempre DEPOIS do registro
    anterior, respeitando a ordem informada). O r da correlação vai no relatório.

Como usar
---------
a) Edite o bloco CONFIG abaixo e rode (Spyder/VSCode/terminal):  python analise_equilibrio.py
b) Ou pela linha de comando, ex.:
   python analise_equilibrio.py --modo separado \
       --celular Socorro_acel.csv \
       --plataforma SocorroPJOA.txt SocorroPJOF.txt SocorroPSOA.txt SocorroPSOF.txt \
       --ordem JOA JOF SOA SOF --sujeito Socorro

   python analise_equilibrio.py --modo unico --celular acel.csv --plataforma plat.txt \
       --ordem OA OF JOA JOF TOA TOF --sujeito Fulano
   Se --ordem não for dado, o script pergunta.

Requisitos: numpy, pandas, scipy, matplotlib, openpyxl
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse
from scipy.signal import butter, filtfilt, detrend, welch
from scipy.stats import chi2

# =============================================================================
# CONFIG — edite aqui se for rodar sem linha de comando
# =============================================================================
CONFIG = dict(
    sujeito="Socorro",
    modo="separado",                       # "unico" ou "separado"
    celular="Socorro__acelData_20261021_162713.csv",
    plataforma=["SocorroPJOA.txt", "SocorroPJOF.txt", "SocorroPSOA.txt", "SocorroPSOF.txt"],
    ordem=["JOA", "JOF", "SOA", "SOF"],    # ordem de coleta DESTE participante
    saida="resultados",                    # pasta de saída

    # --- processamento ---
    fs=100.0,                  # Hz, reamostragem comum
    corte_passa_baixa=6.0,     # Hz (artigo: 6 Hz)
    ordem_filtro=4,
    duracao_analise=30.0,      # s analisados por condição: os ÚLTIMOS N s de cada registro
                               # (o início tem a subida na plataforma / acomodação dos pés)
                               # None = do instante em que a Fz estabiliza até o fim

    # --- eixos ---
    # Plataforma: COPx = ML, COPy = AP (confirmado nos dados: pés juntos -> COPx oscila mais)
    # Celular na lombar: Y = vertical, Z = AP, X = ML.  Sinais ajustados para seguir o COP
    # (a correlação COPx x X do celular é negativa, por isso ML_cel = -X).
    eixo_vertical_cel="Y", eixo_ap_cel="Z", eixo_ml_cel="X",
    sinal_ap_cel=+1, sinal_ml_cel=-1,
    unidade_cop="cm",

    # --- salto ---
    janela_salto_plat=(0, 20),   # s: onde procurar o pico de Fz do salto no arquivo da plataforma
    janela_salto_cel=(0, 60),    # s: onde procurar o pico vertical do salto no celular

    # --- MODO ÚNICO: localização dos registros ---
    # "auto"   = blocos em que a pessoa está sobre a plataforma (Fz > 50% do peso) por >= 20 s
    # "agenda" = inicio_primeiro (s após o salto) + n*(duracao_registro + intervalo)
    # "manual" = lista inicios_manuais (s no tempo da plataforma)
    segmentacao="auto",
    duracao_registro=40.0,
    intervalo=3.0,
    inicio_primeiro=None,        # s após o pico do salto (agenda); None = logo após estabilizar
    inicios_manuais=None,        # ex.: [12.0, 55.0, 98.0, 141.0, 184.0, 227.0]

    # --- condições em que a pessoa ficou GIRADA 90° sobre a plataforma (ex.: tandem) ---
    # Nelas: AP = COPx e ML = -COPy. Conferir pela aba "Concordancia_eixos" do Excel.
    rotacao_90=[],               # ex.: ["TOA", "TOF"]
)

BANDAS = {"LF": (0.0, 0.5), "MF": (0.5, 2.0), "HF": (2.0, 8.0)}
FMAX_FREQ = 8.0


# =============================================================================
# Leitura
# =============================================================================
def ler_plataforma(caminho):
    d = pd.read_csv(caminho, sep=None, engine="python")
    d.columns = [c.strip() for c in d.columns]
    col_t = [c for c in d.columns if c.lower().startswith("time") or c.lower().startswith("tempo")][0]
    d = d.rename(columns={col_t: "t"})
    for c in ["Fz", "COPx", "COPy"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    return d[["t", "Fz", "COPx", "COPy"]].reset_index(drop=True)


def ler_celular(caminho, fs):
    d = pd.read_csv(caminho, sep=None, engine="python")
    d.columns = [c.strip() for c in d.columns]
    col_t = [c for c in d.columns if "tempo" in c.lower() or "time" in c.lower()][0]
    t = d[col_t].to_numpy(float)
    if np.nanmedian(np.diff(t)) > 0.5:      # está em ms
        t = t / 1000.0
    ordem = np.argsort(t)
    t = t[ordem]
    t, idx = np.unique(t, return_index=True)
    tt = np.arange(t[0], t[-1], 1.0 / fs)
    out = pd.DataFrame({"t": tt})
    for ax in ["X", "Y", "Z"]:
        out[ax] = np.interp(tt, t, d[ax].to_numpy(float)[ordem][idx])
    fs_orig = 1.0 / np.median(np.diff(t))
    return out, fs_orig


def reamostrar_plat(d, fs):
    tt = np.arange(d.t.iloc[0], d.t.iloc[-1], 1.0 / fs)
    out = pd.DataFrame({"t": tt})
    for c in ["Fz", "COPx", "COPy"]:
        s = d[c].interpolate(limit_direction="both")
        out[c] = np.interp(tt, d.t.values, s.values)
    return out


# =============================================================================
# Sincronização
# =============================================================================
def pico_salto_plat(d, janela):
    m = (d.t >= d.t.iloc[0] + janela[0]) & (d.t <= d.t.iloc[0] + janela[1])
    i = d.Fz[m].idxmax()
    return d.t[i], d.Fz[i]


def pico_salto_cel(c, eixo_v, janela):
    m = (c.t >= c.t.iloc[0] + janela[0]) & (c.t <= c.t.iloc[0] + janela[1])
    i = c[eixo_v][m].idxmax()
    return c.t[i], c[eixo_v][i]


def tem_salto(d, peso):
    """Salto -> pico de Fz bem acima do peso corporal (>1.8x)."""
    return d.Fz.max() > 1.8 * peso


def _bp(x, fs):
    b, a = butter(2, [0.1, 3.0], btype="band", fs=fs)
    return filtfilt(b, a, x)


def offset_por_correlacao(seg_plat, cel, cfg, t_busca_min):
    """Acha o deslocamento (t_cel - t_plat) que maximiza a correlação COP x aceleração."""
    fs = cfg["fs"]
    ap_c = _bp(seg_plat.COPy.values, fs)
    ml_c = _bp(seg_plat.COPx.values, fs)
    ap_a = _bp(cfg["sinal_ap_cel"] * cel[cfg["eixo_ap_cel"]].values, fs)
    ml_a = _bp(cfg["sinal_ml_cel"] * cel[cfg["eixo_ml_cel"]].values, fs)
    n = len(ap_c)
    i0 = max(0, int(np.searchsorted(cel.t.values, t_busca_min)))
    passo = 2
    from numpy.lib.stride_tricks import sliding_window_view

    def corr(sig, ref):
        W = sliding_window_view(sig[i0:], n)[::passo]
        Wc = W - W.mean(1, keepdims=True)
        r = ref - ref.mean()
        return (Wc @ r) / (np.linalg.norm(Wc, axis=1) * np.linalg.norm(r) + 1e-12)

    r_ap = corr(ap_a, ap_c)
    r_ml = corr(ml_a, ml_c)
    score = r_ap + r_ml
    k = int(np.argmax(score))
    j = i0 + k * passo
    # refino amostra a amostra
    melhor = (-9, j)
    for jj in range(max(0, j - passo), min(len(ap_a) - n, j + passo) + 1):
        s = np.corrcoef(ap_a[jj:jj + n], ap_c)[0, 1] + np.corrcoef(ml_a[jj:jj + n], ml_c)[0, 1]
        if s > melhor[0]:
            melhor = (s, jj)
    j = melhor[1]
    r_ap_f = np.corrcoef(ap_a[j:j + n], ap_c)[0, 1]
    r_ml_f = np.corrcoef(ml_a[j:j + n], ml_c)[0, 1]
    return cel.t.values[j] - seg_plat.t.values[0], r_ap_f, r_ml_f


# =============================================================================
# Segmentação
# =============================================================================
def inicio_estavel(d, peso, tol=0.15, min_s=3.0, fs=100.0):
    """Primeiro instante a partir do qual a Fz fica dentro de ±tol do peso por >= min_s."""
    ok = (np.abs(d.Fz.values - peso) < tol * peso).astype(int)
    n = int(min_s * fs)
    run = np.convolve(ok, np.ones(n, int), "valid")
    idx = np.where(run == n)[0]
    return d.t.values[idx[0]] if len(idx) else d.t.values[0]


def fim_estavel(d, peso, tol=0.15, min_s=3.0, fs=100.0):
    """Último instante até o qual a Fz ficou dentro de ±tol do peso (antes de descer da plataforma)."""
    ok = (np.abs(d.Fz.values - peso) < tol * peso).astype(int)
    n = int(min_s * fs)
    run = np.convolve(ok, np.ones(n, int), "valid")
    idx = np.where(run == n)[0]
    return d.t.values[idx[-1] + n - 1] if len(idx) else d.t.values[-1]


def janela_analise(seg, peso, cfg, cond=""):
    """Últimos `duracao_analise` s ESTÁVEIS do registro (corta subida e descida da plataforma)."""
    fs = cfg["fs"]
    t_est = inicio_estavel(seg, peso, fs=fs) + 1.0
    t1 = fim_estavel(seg, peso, fs=fs)
    if t1 < seg.t.iloc[-1] - 0.05:          # a pessoa desceu da plataforma: margem de segurança
        t1 -= cfg.get("margem_descida", 2.0)
    if not cfg["duracao_analise"]:
        return t_est, t1
    t0 = t1 - cfg["duracao_analise"]
    if t0 < t_est:
        if cond:
            print(f"  AVISO {cond}: só {t1 - t_est:.1f} s estáveis (< {cfg['duracao_analise']} s); "
                  f"analisado apenas o trecho estável")
        t0 = t_est
    return t0, t1


def blocos_sobre_plataforma(d, peso, min_dur=20.0):
    on = d.Fz.values > 0.5 * peso
    blocos, i = [], 0
    while i < len(on):
        if on[i]:
            j = i
            while j < len(on) and on[j]:
                j += 1
            if d.t.values[j - 1] - d.t.values[i] >= min_dur:
                blocos.append((d.t.values[i], d.t.values[j - 1]))
            i = j
        else:
            i += 1
    return blocos


# =============================================================================
# Variáveis de equilíbrio
# =============================================================================
def preprocessar(x, fs, fc, ordem):
    x = detrend(np.asarray(x, float))
    b, a = butter(ordem, fc / (fs / 2.0), btype="low")
    return filtfilt(b, a, x)


def elipse95(ap, ml):
    cov = np.cov(np.vstack([ml, ap]))
    val, vec = np.linalg.eigh(cov)
    k = chi2.ppf(0.95, 2)
    area = np.pi * k * np.sqrt(np.prod(val))
    larg, alt = 2 * np.sqrt(k * val)
    ang = np.degrees(np.arctan2(vec[1, 1], vec[0, 1]))
    return area, (larg, alt, ang)


def espectro(x, fs):
    nper = int(min(len(x), 20 * fs))
    f, P = welch(x, fs=fs, nperseg=nper, noverlap=nper // 2, detrend="constant")
    return f, P


def freq_mediana(f, P, fmax=FMAX_FREQ):
    m = f <= fmax
    c = np.cumsum(P[m])
    return f[m][np.searchsorted(c, c[-1] / 2.0)]


def energia_banda(f, P, f1, f2):
    df = f[1] - f[0]
    m = (f >= f1) & (f < f2)
    return np.sum(P[m]) * df


def variaveis(ap, ml, fs):
    dur = len(ap) / fs
    path = np.sum(np.sqrt(np.diff(ap) ** 2 + np.diff(ml) ** 2))
    area, _ = elipse95(ap, ml)
    fA, PA = espectro(ap, fs)
    fM, PM = espectro(ml, fs)
    v = {
        "Duracao_s": dur,
        "RMS_AP": np.sqrt(np.mean(ap ** 2)),
        "RMS_ML": np.sqrt(np.mean(ml ** 2)),
        "Amplitude_AP": np.ptp(ap),
        "Amplitude_ML": np.ptp(ml),
        "Path_total": path,
        "Vel_media": path / dur,
        "Area_elipse95": area,
        "MedFreq_AP_Hz": freq_mediana(fA, PA),
        "MedFreq_ML_Hz": freq_mediana(fM, PM),
    }
    for b, (f1, f2) in BANDAS.items():
        v[f"Energia_{b}_AP"] = energia_banda(fA, PA, f1, f2)
    for b, (f1, f2) in BANDAS.items():
        v[f"Energia_{b}_ML"] = energia_banda(fM, PM, f1, f2)
    return v


# =============================================================================
# Gráficos
# =============================================================================
def plot_condicao(cond, t, cop_ap, cop_ml, acc_ap, acc_ml, un_cop, arq):
    fig, axs = plt.subplots(2, 3, figsize=(15, 8), gridspec_kw=dict(width_ratios=[1.6, 1.6, 1]))
    linhas = [("COP — plataforma", cop_ap, cop_ml, un_cop, "#1f5a96"),
              ("COM — acelerômetro do celular", acc_ap, acc_ml, "m/s²", "#b5482b")]
    for r, (nome, ap, ml, un, cor) in enumerate(linhas):
        axs[r, 0].plot(t, ap, color=cor, lw=0.8)
        axs[r, 0].set(title=f"{nome}: AP x tempo", xlabel="tempo (s)", ylabel=f"AP ({un})")
        axs[r, 1].plot(t, ml, color=cor, lw=0.8)
        axs[r, 1].set(title=f"{nome}: ML x tempo", xlabel="tempo (s)", ylabel=f"ML ({un})")
        ax = axs[r, 2]
        ax.plot(ml, ap, color=cor, lw=0.6)
        area, (w, h, ang) = elipse95(ap, ml)
        ax.add_patch(Ellipse((ml.mean(), ap.mean()), w, h, angle=ang, fc=cor, alpha=0.18, ec=cor))
        lim = max(np.abs(ap).max(), np.abs(ml).max()) * 1.15
        ax.set(xlim=(-lim, lim), ylim=(-lim, lim), aspect="equal",
               title=f"AP x ML (elipse 95% = {area:.3g} " + (f"({un})²" if "/" in un else f"{un}²") + ")",
               xlabel=f"ML ({un})", ylabel=f"AP ({un})")
        for a in axs[r]:
            a.grid(alpha=0.3)
    fig.suptitle(f"Condição {cond}", fontsize=14, weight="bold")
    fig.tight_layout()
    fig.savefig(arq, dpi=150)
    plt.close(fig)


def plot_salto(plat, cel, t_pp, t_pc, eixo_v, arq):
    fig, ax = plt.subplots(figsize=(10, 4))
    off = t_pc - t_pp
    m = (plat.t > t_pp - 3) & (plat.t < t_pp + 3)
    mc = (cel.t > t_pc - 3) & (cel.t < t_pc + 3)
    ax.plot(plat.t[m] - t_pp, plat.Fz[m] / plat.Fz[m].max(), label="Fz plataforma (normalizada)")
    ax.plot(cel.t[mc] - t_pc, cel[eixo_v][mc] / cel[eixo_v][mc].max(), label=f"{eixo_v} celular (normalizado)")
    ax.axvline(0, color="k", ls="--", lw=0.8)
    ax.set(xlabel="tempo relativo ao pico do salto (s)",
           title=f"Sincronização pelo salto — offset celular−plataforma = {off:.3f} s")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(arq, dpi=150)
    plt.close(fig)


def plot_visao_geral(cel, janelas, eixo_v, arq):
    fig, ax = plt.subplots(figsize=(14, 4))
    ax.plot(cel.t, cel[eixo_v], lw=0.4, color="0.3")
    cores = plt.cm.tab10.colors
    for k, (cond, t0, t1) in enumerate(janelas):
        ax.axvspan(t0, t1, color=cores[k % 10], alpha=0.25)
        ax.text((t0 + t1) / 2, ax.get_ylim()[1], cond, ha="center", va="top", weight="bold")
    ax.set(xlabel="tempo do celular (s)", ylabel=f"{eixo_v} (m/s²)",
           title="Registro completo do celular com as janelas analisadas")
    fig.tight_layout()
    fig.savefig(arq, dpi=150)
    plt.close(fig)


# =============================================================================
# Pipeline
# =============================================================================
def analisar(cfg):
    fs = cfg["fs"]
    os.makedirs(cfg["saida"], exist_ok=True)
    pref = os.path.join(cfg["saida"], cfg["sujeito"])
    ordem = list(cfg["ordem"])

    cel, fs_cel = ler_celular(cfg["celular"], fs)
    print(f"Celular: {len(cel)/fs:.1f} s, fs original ≈ {fs_cel:.0f} Hz (reamostrado a {fs:.0f} Hz)")
    t_pc, _ = pico_salto_cel(cel, cfg["eixo_vertical_cel"], cfg["janela_salto_cel"])
    print(f"Pico do salto no celular: {t_pc:.3f} s")

    # ---------- montar a lista de (condição, trecho da plataforma, offset) ----------
    trechos, sinc = [], []
    plats = cfg["plataforma"] if isinstance(cfg["plataforma"], (list, tuple)) else [cfg["plataforma"]]

    if cfg["modo"] == "unico":
        plat = reamostrar_plat(ler_plataforma(plats[0]), fs)
        peso = np.median(plat.Fz[plat.Fz > 0.5 * plat.Fz.max() * 0.2])
        t_pp, fz_pk = pico_salto_plat(plat, cfg["janela_salto_plat"])
        off = t_pc - t_pp
        plot_salto(plat, cel, t_pp, t_pc, cfg["eixo_vertical_cel"], pref + "_sincronizacao_salto.png")
        print(f"Pico do salto na plataforma: {t_pp:.3f} s  -> offset = {off:.3f} s")

        if cfg["segmentacao"] == "manual":
            inicios = list(cfg["inicios_manuais"])
            fins = [i + cfg["duracao_registro"] for i in inicios]
        elif cfg["segmentacao"] == "agenda":
            t1 = t_pp + cfg["inicio_primeiro"] if cfg["inicio_primeiro"] is not None else \
                inicio_estavel(plat[plat.t > t_pp + 0.5], peso, fs=fs)
            passo = cfg["duracao_registro"] + cfg["intervalo"]
            inicios = [t1 + k * passo for k in range(len(ordem))]
            fins = [i + cfg["duracao_registro"] for i in inicios]
        else:
            bl = blocos_sobre_plataforma(plat[plat.t > t_pp + 0.5], peso)
            if len(bl) != len(ordem):
                sys.exit(f"Segmentação automática achou {len(bl)} blocos e a ordem tem {len(ordem)} "
                         f"condições. Use segmentacao='agenda' ou 'manual'. Blocos: {bl}")
            inicios, fins = [b[0] for b in bl], [b[1] for b in bl]
        for cond, i0, i1 in zip(ordem, inicios, fins):
            seg = plat[(plat.t >= i0) & (plat.t < i1)].reset_index(drop=True)
            trechos.append((cond, seg, off, peso))
            sinc.append(dict(Condicao=cond, Metodo="salto", Offset_s=off, r_AP=np.nan, r_ML=np.nan,
                             Arquivo=os.path.basename(plats[0])))

    else:  # separado
        if len(plats) != len(ordem):
            sys.exit(f"{len(plats)} arquivos de plataforma mas {len(ordem)} condições na ordem.")
        t_min = cel.t.iloc[0]
        for cond, arq in zip(ordem, plats):
            plat = reamostrar_plat(ler_plataforma(arq), fs)
            peso = np.median(plat.Fz[plat.t > plat.t.iloc[-1] - 20])
            if tem_salto(plat, peso):
                t_pp, _ = pico_salto_plat(plat, cfg["janela_salto_plat"])
                off = t_pc - t_pp
                metodo, rap, rml = "salto", np.nan, np.nan
                plot_salto(plat, cel, t_pp, t_pc, cfg["eixo_vertical_cel"], pref + "_sincronizacao_salto.png")
            else:
                t0c, t1c = janela_analise(plat, peso, cfg)
                seg_c = plat[(plat.t >= t0c) & (plat.t < t1c)].reset_index(drop=True)
                off, rap, rml = offset_por_correlacao(seg_c, cel, cfg, t_min)
                metodo = "correlacao COP x aceleracao"
            sinc.append(dict(Condicao=cond, Metodo=metodo, Offset_s=off, r_AP=rap, r_ML=rml,
                             Arquivo=os.path.basename(arq)))
            print(f"{cond}: {metodo}, offset = {off:.3f} s"
                  + ("" if np.isnan(rap) else f" (r_AP={rap:.2f}, r_ML={rml:.2f})"))
            trechos.append((cond, plat, off, peso))
            t_min = plat.t.iloc[-1] + off   # próximo registro só depois deste

    # ---------- análise de cada condição ----------
    linhas, janelas, sinais, concord = [], [], {}, []
    un = cfg["unidade_cop"]
    for cond, seg, off, peso in trechos:
        t0, t1 = janela_analise(seg, peso, cfg, cond)
        s = seg[(seg.t >= t0) & (seg.t < t1)]
        c = cel[(cel.t >= t0 + off) & (cel.t < t1 + off)]
        n = min(len(s), len(c))
        s, c = s.iloc[:n], c.iloc[:n]
        fc, od = cfg["corte_passa_baixa"], cfg["ordem_filtro"]
        if cond in cfg.get("rotacao_90", []):
            cop_ap = preprocessar(s.COPx, fs, fc, od)
            cop_ml = preprocessar(-s.COPy, fs, fc, od)
        else:
            cop_ap = preprocessar(s.COPy, fs, fc, od)
            cop_ml = preprocessar(s.COPx, fs, fc, od)
        acc_ap = preprocessar(cfg["sinal_ap_cel"] * c[cfg["eixo_ap_cel"]], fs, fc, od)
        acc_ml = preprocessar(cfg["sinal_ml_cel"] * c[cfg["eixo_ml_cel"]], fs, fc, od)
        cc = lambda a, b: np.corrcoef(a, b)[0, 1]
        concord.append(dict(Condicao=cond, COP_eixos_girados=cond in cfg.get("rotacao_90", []),
                            r_AP_AP=cc(cop_ap, acc_ap), r_ML_ML=cc(cop_ml, acc_ml),
                            r_COPAP_ACCML=cc(cop_ap, acc_ml), r_COPML_ACCAP=cc(cop_ml, acc_ap)))
        if abs(concord[-1]["r_COPML_ACCAP"]) > abs(concord[-1]["r_ML_ML"]) + 0.2 and \
           abs(concord[-1]["r_COPAP_ACCML"]) > abs(concord[-1]["r_AP_AP"]) + 0.2:
            print(f"  AVISO {cond}: eixos do COP parecem girados 90° em relação ao celular "
                  f"(considere --rotacao_90 {cond})")
        tt = np.arange(n) / fs

        for fonte, ap, ml, u in [("COP", cop_ap, cop_ml, un), ("ACC_celular", acc_ap, acc_ml, "m/s²")]:
            v = variaveis(ap, ml, fs)
            linhas.append(dict(Sujeito=cfg["sujeito"], Condicao=cond, Fonte=fonte, Unidade=u,
                               Inicio_plat_s=round(t0, 2), Inicio_cel_s=round(t0 + off, 2), **v))
        janelas.append((cond, t0 + off, t1 + off))
        sinais[cond] = pd.DataFrame(dict(tempo_s=tt, COP_AP=cop_ap, COP_ML=cop_ml,
                                         ACC_AP=acc_ap, ACC_ML=acc_ml))
        plot_condicao(cond, tt, cop_ap, cop_ml, acc_ap, acc_ml, un, f"{pref}_{cond}.png")

    plot_visao_geral(cel, janelas, cfg["eixo_vertical_cel"], pref + "_visao_geral.png")

    # ---------- Excel ----------
    res = pd.DataFrame(linhas)
    largo = res.drop(columns=["Unidade", "Inicio_plat_s", "Inicio_cel_s"]).melt(
        id_vars=["Sujeito", "Condicao", "Fonte"], var_name="Variavel")
    largo["col"] = largo.Fonte + "_" + largo.Variavel + "_" + largo.Condicao
    uma_linha = largo.pivot_table(index="Sujeito", columns="col", values="value", sort=False).reset_index()

    xls = pref + "_resultados.xlsx"
    with pd.ExcelWriter(xls) as w:
        res.to_excel(w, sheet_name="Variaveis", index=False)
        uma_linha.to_excel(w, sheet_name="Linha_unica_sujeito", index=False)
        pd.DataFrame(sinc).to_excel(w, sheet_name="Sincronizacao", index=False)
        pd.DataFrame(concord).round(3).to_excel(w, sheet_name="Concordancia_eixos", index=False)
        pd.DataFrame([{k: str(v) for k, v in cfg.items()}]).T.to_excel(w, sheet_name="Parametros", header=["valor"])
        for cond, df in sinais.items():
            df.to_excel(w, sheet_name=f"Sinal_{cond}"[:31], index=False)
    print(f"\nResultados salvos em: {os.path.abspath(cfg['saida'])}")
    return res


def main():
    p = argparse.ArgumentParser(description="Análise COP (plataforma) + COM (celular)")
    p.add_argument("--modo", choices=["unico", "separado"])
    p.add_argument("--celular")
    p.add_argument("--plataforma", nargs="+")
    p.add_argument("--ordem", nargs="+", help="ex.: OA OF JOA JOF TOA TOF")
    p.add_argument("--sujeito")
    p.add_argument("--saida")
    p.add_argument("--segmentacao", choices=["auto", "agenda", "manual"])
    p.add_argument("--inicios", nargs="+", type=float, help="inícios manuais (s, tempo da plataforma)")
    p.add_argument("--duracao_analise", type=float)
    p.add_argument("--rotacao_90", nargs="+", help="condições com a pessoa girada 90° na plataforma, ex.: TOA TOF")
    a = p.parse_args()

    cfg = dict(CONFIG)
    for k in ["modo", "celular", "plataforma", "ordem", "sujeito", "saida", "segmentacao", "duracao_analise", "rotacao_90"]:
        if getattr(a, k) is not None:
            cfg[k] = getattr(a, k)
    if a.inicios:
        cfg["inicios_manuais"], cfg["segmentacao"] = a.inicios, "manual"
    if not cfg["ordem"]:
        txt = input("Ordem das condições deste participante (ex.: OA OF JOA JOF TOA TOF): ")
        cfg["ordem"] = txt.replace(",", " ").split()
    cfg["ordem"] = [o.strip().upper() for o in cfg["ordem"]]
    cfg["rotacao_90"] = [o.strip().upper() for o in (cfg.get("rotacao_90") or [])]
    print(f"Sujeito: {cfg['sujeito']} | modo: {cfg['modo']} | ordem: {cfg['ordem']}")
    analisar(cfg)


if __name__ == "__main__":
    main()
