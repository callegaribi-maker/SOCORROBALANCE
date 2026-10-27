# -*- coding: utf-8 -*-
"""
Núcleo de cálculo: equilíbrio estático com plataforma de força (COP) + acelerômetro de celular (COM).

Funções puras (sem Streamlit), usadas pelo app (app.py) e pela linha de comando (analise_equilibrio.py).
Variáveis baseadas em Farias et al., Lepr Rev 2026 e Quijoux et al., 2021.
"""
import io

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view
from scipy.signal import butter, filtfilt, detrend, welch
from scipy.stats import chi2

BANDAS = {"LF": (0.0, 0.5), "MF": (0.5, 2.0), "HF": (2.0, 8.0)}
FMAX_FREQ = 8.0

FILTRO_PADRAO = dict(detrend=True, passa_baixa=6.0, passa_alta=None, ordem=4)


# =============================================================================
# Leitura
# =============================================================================
def _ler_tabela(arquivo):
    if hasattr(arquivo, "read"):
        conteudo = arquivo.read()
        if hasattr(arquivo, "seek"):
            arquivo.seek(0)
        if isinstance(conteudo, bytes):
            conteudo = conteudo.decode("utf-8", errors="replace")
        arquivo = io.StringIO(conteudo)
    d = pd.read_csv(arquivo, sep=None, engine="python")
    d.columns = [str(c).strip() for c in d.columns]
    return d


def _coluna_tempo(d):
    for c in d.columns:
        cl = c.lower()
        if cl.startswith("time") or cl.startswith("tempo") or cl == "t":
            return c
    return d.columns[0]


def tipo_arquivo(conteudo):
    """'plataforma', 'celular' ou None, pelas colunas do cabeçalho."""
    if isinstance(conteudo, bytes):
        conteudo = conteudo[:5000].decode("utf-8", errors="replace")
    cab = conteudo.splitlines()[0] if conteudo else ""
    cols = {c.strip().strip('"').lower() for c in cab.replace("\t", ",").replace(";", ",").split(",")}
    if {"fz", "copx", "copy"} <= cols:
        return "plataforma"
    if {"x", "y", "z"} <= cols:
        return "celular"
    return None


def ler_plataforma(arquivo):
    """Arquivo da plataforma: Time (s), Fz, COPx, COPy (tab ou vírgula)."""
    d = _ler_tabela(arquivo)
    d = d.rename(columns={_coluna_tempo(d): "t"})
    for c in ["t", "Fz", "COPx", "COPy"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["t"]).sort_values("t")
    return d[["t", "Fz", "COPx", "COPy"]].reset_index(drop=True)


def ler_celular(arquivo):
    """Arquivo do celular: TempoMs (ou tempo em s), X, Y, Z."""
    d = _ler_tabela(arquivo)
    ct = _coluna_tempo(d)
    t = pd.to_numeric(d[ct], errors="coerce").to_numpy(float)
    if "ms" in ct.lower() or np.nanmedian(np.diff(t)) > 0.5:
        t = t / 1000.0
    out = pd.DataFrame({"t": t})
    for ax in ["X", "Y", "Z"]:
        out[ax] = pd.to_numeric(d[ax], errors="coerce")
    out = out.dropna().sort_values("t").drop_duplicates("t")
    return out.reset_index(drop=True)


def fs_original(d):
    return 1.0 / np.median(np.diff(d.t.values))


def reamostrar(d, fs):
    """Reamostra todas as colunas (exceto t) numa grade uniforme de fs Hz."""
    tt = np.arange(d.t.iloc[0], d.t.iloc[-1], 1.0 / fs)
    out = pd.DataFrame({"t": tt})
    for c in d.columns:
        if c == "t":
            continue
        s = d[c].interpolate(limit_direction="both")
        out[c] = np.interp(tt, d.t.values, s.values)
    return out


# =============================================================================
# Sincronização
# =============================================================================
def pico_salto(d, coluna, janela=None):
    """Instante do maior valor de `coluna` dentro de janela=(ini, fim) em s relativos ao início."""
    m = np.ones(len(d), bool)
    if janela:
        m = (d.t >= d.t.iloc[0] + janela[0]) & (d.t <= d.t.iloc[0] + janela[1])
    i = d[coluna][m].idxmax()
    return float(d.t[i])


def peso_corporal(plat):
    fz = plat.Fz.values
    return float(np.median(fz[fz > 0.3 * np.nanpercentile(fz, 99)]))


def tem_salto(plat, peso):
    return plat.Fz.max() > 1.8 * peso


def _bp(x, fs):
    b, a = butter(2, [0.1, 3.0], btype="band", fs=fs)
    return filtfilt(b, a, x)


def offset_por_correlacao(seg_plat, cel, fs, eixos, t_busca_min=None, t_busca_max=None, rot90=False):
    """Deslocamento (t_cel - t_plat) que maximiza a correlação COP x aceleração (ambos a fs Hz)."""
    ap_c, ml_c = cop_ap_ml(seg_plat, rot90)
    ap_c, ml_c = _bp(ap_c, fs), _bp(ml_c, fs)
    ap_a, ml_a = acc_ap_ml(cel, eixos)
    ap_a, ml_a = _bp(ap_a, fs), _bp(ml_a, fs)
    n = len(ap_c)
    i0 = 0 if t_busca_min is None else max(0, int(np.searchsorted(cel.t.values, t_busca_min)))
    i1 = len(ap_a) if t_busca_max is None else min(len(ap_a), int(np.searchsorted(cel.t.values, t_busca_max)) + n)
    if i1 - i0 <= n:
        return np.nan, np.nan, np.nan
    passo = 2

    def corr(sig, ref):
        W = sliding_window_view(sig[i0:i1], n)[::passo]
        Wc = W - W.mean(1, keepdims=True)
        r = ref - ref.mean()
        return (Wc @ r) / (np.linalg.norm(Wc, axis=1) * np.linalg.norm(r) + 1e-12)

    k = int(np.argmax(corr(ap_a, ap_c) + corr(ml_a, ml_c)))
    j = i0 + k * passo
    melhor = (-9, j)
    for jj in range(max(0, j - passo), min(len(ap_a) - n, j + passo) + 1):
        s = np.corrcoef(ap_a[jj:jj + n], ap_c)[0, 1] + np.corrcoef(ml_a[jj:jj + n], ml_c)[0, 1]
        if s > melhor[0]:
            melhor = (s, jj)
    j = melhor[1]
    return (float(cel.t.values[j] - seg_plat.t.values[0]),
            float(np.corrcoef(ap_a[j:j + n], ap_c)[0, 1]),
            float(np.corrcoef(ml_a[j:j + n], ml_c)[0, 1]))


# =============================================================================
# Segmentação
# =============================================================================
def _runs_ok(d, peso, tol, min_s, fs):
    ok = (np.abs(d.Fz.values - peso) < tol * peso).astype(int)
    n = max(1, int(min_s * fs))
    run = np.convolve(ok, np.ones(n, int), "valid")
    return np.where(run == n)[0], n


def inicio_estavel(d, peso, tol=0.15, min_s=3.0, fs=100.0):
    idx, _ = _runs_ok(d, peso, tol, min_s, fs)
    return float(d.t.values[idx[0]]) if len(idx) else float(d.t.values[0])


def fim_estavel(d, peso, tol=0.15, min_s=3.0, fs=100.0):
    idx, n = _runs_ok(d, peso, tol, min_s, fs)
    return float(d.t.values[idx[-1] + n - 1]) if len(idx) else float(d.t.values[-1])


def blocos_sobre_plataforma(d, peso, min_dur=15.0):
    on = d.Fz.values > 0.5 * peso
    t = d.t.values
    blocos, i = [], 0
    while i < len(on):
        if on[i]:
            j = i
            while j < len(on) and on[j]:
                j += 1
            if t[j - 1] - t[i] >= min_dur:
                blocos.append((float(t[i]), float(t[j - 1])))
            i = j
        else:
            i += 1
    return blocos


def janela_estavel(seg, peso, fs, duracao=30.0, margem_subida=1.0, margem_descida=2.0):
    """Últimos `duracao` s estáveis de um bloco (corta subida e descida da plataforma)."""
    t_ini = inicio_estavel(seg, peso, fs=fs) + margem_subida
    t_fim = fim_estavel(seg, peso, fs=fs)
    if t_fim < seg.t.iloc[-1] - 0.05:
        t_fim -= margem_descida
    if not duracao:
        return t_ini, t_fim
    return max(t_ini, t_fim - duracao), t_fim


def segmentar_auto(plat, peso, fs, t_salto=None, duracao=30.0):
    """Lista de (t0, t1) — uma janela por bloco em pé na plataforma, depois do salto."""
    d = plat if t_salto is None else plat[plat.t > t_salto + 0.5]
    out = []
    for b0, b1 in blocos_sobre_plataforma(d, peso):
        seg = d[(d.t >= b0) & (d.t <= b1)]
        out.append(janela_estavel(seg, peso, fs, duracao))
    return out


def segmentar_agenda(t_inicio, n, duracao_registro=40.0, intervalo=3.0, duracao=30.0):
    out = []
    for k in range(n):
        a = t_inicio + k * (duracao_registro + intervalo)
        b = a + duracao_registro
        out.append((max(a, b - duracao) if duracao else a, b))
    return out


# =============================================================================
# Eixos, filtragem e variáveis
# =============================================================================
EIXOS_PADRAO = dict(vertical="Y", ap="Z", ml="X", sinal_ap=+1, sinal_ml=-1)


def cop_ap_ml(s, rot90=False):
    """Plataforma: COPx = ML, COPy = AP. Girada 90°: AP = COPx, ML = -COPy."""
    if rot90:
        return s.COPx.to_numpy(float), -s.COPy.to_numpy(float)
    return s.COPy.to_numpy(float), s.COPx.to_numpy(float)


def acc_ap_ml(c, eixos):
    return (eixos["sinal_ap"] * c[eixos["ap"]].to_numpy(float),
            eixos["sinal_ml"] * c[eixos["ml"]].to_numpy(float))


def filtrar(x, fs, detrend_on=True, passa_baixa=6.0, passa_alta=None, ordem=4):
    x = np.asarray(x, float)
    x = detrend(x) if detrend_on else x - x.mean()
    ny = fs / 2.0
    if passa_alta and passa_baixa and passa_alta > 0:
        b, a = butter(ordem, [passa_alta / ny, passa_baixa / ny], btype="band")
        return filtfilt(b, a, x)
    if passa_baixa and passa_baixa < ny:
        b, a = butter(ordem, passa_baixa / ny, btype="low")
        x = filtfilt(b, a, x)
    if passa_alta and passa_alta > 0:
        b, a = butter(ordem, passa_alta / ny, btype="high")
        x = filtfilt(b, a, x)
    return x


def elipse95(ap, ml):
    cov = np.cov(np.vstack([ml, ap]))
    val, vec = np.linalg.eigh(cov)
    k = chi2.ppf(0.95, 2)
    area = float(np.pi * k * np.sqrt(np.prod(np.clip(val, 0, None))))
    larg, alt = 2 * np.sqrt(k * np.clip(val, 0, None))
    ang = float(np.degrees(np.arctan2(vec[1, 1], vec[0, 1])))
    return area, (float(larg), float(alt), ang)


def contorno_elipse(ap, ml, n=100):
    _, (w, h, ang) = elipse95(ap, ml)
    th = np.linspace(0, 2 * np.pi, n)
    x, y = w / 2 * np.cos(th), h / 2 * np.sin(th)
    a = np.radians(ang)
    return (ml.mean() + x * np.cos(a) - y * np.sin(a),
            ap.mean() + x * np.sin(a) + y * np.cos(a))


def espectro(x, fs):
    nper = int(min(len(x), 20 * fs))
    return welch(x, fs=fs, nperseg=nper, noverlap=nper // 2, detrend="constant")


def freq_mediana(f, P, fmax=FMAX_FREQ):
    m = f <= fmax
    c = np.cumsum(P[m])
    return float(f[m][np.searchsorted(c, c[-1] / 2.0)])


def energia_banda(f, P, f1, f2):
    m = (f >= f1) & (f < f2)
    return float(np.sum(P[m]) * (f[1] - f[0]))


def variaveis(ap, ml, fs):
    dur = len(ap) / fs
    path = float(np.sum(np.sqrt(np.diff(ap) ** 2 + np.diff(ml) ** 2)))
    fA, PA = espectro(ap, fs)
    fM, PM = espectro(ml, fs)
    v = {
        "Duracao_s": dur,
        "RMS_AP": float(np.sqrt(np.mean(ap ** 2))),
        "RMS_ML": float(np.sqrt(np.mean(ml ** 2))),
        "Amplitude_AP": float(np.ptp(ap)),
        "Amplitude_ML": float(np.ptp(ml)),
        "Path_total": path,
        "Vel_media": path / dur,
        "Area_elipse95": elipse95(ap, ml)[0],
        "MedFreq_AP_Hz": freq_mediana(fA, PA),
        "MedFreq_ML_Hz": freq_mediana(fM, PM),
    }
    for b, (f1, f2) in BANDAS.items():
        v[f"Energia_{b}_AP"] = energia_banda(fA, PA, f1, f2)
    for b, (f1, f2) in BANDAS.items():
        v[f"Energia_{b}_ML"] = energia_banda(fM, PM, f1, f2)
    return v


def processar_condicao(plat, cel, t0, t1, offset, fs, eixos, filtro_cop, filtro_acc, rot90=False):
    """Recorta, filtra e calcula tudo para uma condição. plat/cel já reamostrados a fs."""
    s = plat[(plat.t >= t0) & (plat.t < t1)]
    c = cel[(cel.t >= t0 + offset) & (cel.t < t1 + offset)]
    n = min(len(s), len(c))
    if n < fs * 5:
        raise ValueError(f"janela curta demais ou fora do registro do celular ({n / fs:.1f} s)")
    s, c = s.iloc[:n], c.iloc[:n]
    cap, cml = cop_ap_ml(s, rot90)
    aap, aml = acc_ap_ml(c, eixos)
    cop_ap, cop_ml = filtrar(cap, fs, **filtro_cop), filtrar(cml, fs, **filtro_cop)
    acc_ap, acc_ml = filtrar(aap, fs, **filtro_acc), filtrar(aml, fs, **filtro_acc)
    cc = lambda a, b: float(np.corrcoef(a, b)[0, 1])
    return dict(
        tempo=np.arange(n) / fs,
        cop_ap=cop_ap, cop_ml=cop_ml, acc_ap=acc_ap, acc_ml=acc_ml,
        var_cop=variaveis(cop_ap, cop_ml, fs),
        var_acc=variaveis(acc_ap, acc_ml, fs),
        concordancia=dict(r_AP_AP=cc(cop_ap, acc_ap), r_ML_ML=cc(cop_ml, acc_ml),
                          r_COPAP_ACCML=cc(cop_ap, acc_ml), r_COPML_ACCAP=cc(cop_ml, acc_ap)),
        fz_min=float(s.Fz.min()), fz_max=float(s.Fz.max()),
    )


def suspeita_rotacao(conc, margem=0.2):
    return (abs(conc["r_COPML_ACCAP"]) > abs(conc["r_ML_ML"]) + margem and
            abs(conc["r_COPAP_ACCML"]) > abs(conc["r_AP_AP"]) + margem)
