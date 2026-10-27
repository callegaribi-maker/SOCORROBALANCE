# Equilíbrio estático — COP (plataforma de força) × COM (acelerômetro do celular)

App Streamlit e script de linha de comando para analisar equilíbrio estático com plataforma de força
e acelerômetro de celular fixado no tronco (lombar), registrados ao mesmo tempo.

## O que faz

1. **Importa** o arquivo da plataforma (`Time (s)`, `Fz`, `COPx`, `COPy`) e o do celular
   (`TempoMs`, `X`, `Y`, `Z`). Aceita tab ou vírgula.
2. **Sincroniza** pelo salto inicial: pico de Fz da plataforma × pico do eixo vertical (Y) do celular.
   O offset aparece num gráfico e pode ser ajustado à mão.
3. **Segmenta** as condições (ex.: OA, OF, JOA, JOF, TOA, TOF) em qualquer ordem, informada por participante:
   - automático: um bloco por período em pé na plataforma, analisando os últimos N s estáveis
     (corta a subida e a descida da plataforma);
   - agenda: registro de 40 s + intervalo de 3 s;
   - a tabela é editável: nome, início, fim, incluir/excluir, adicionar linhas, além de um slider para ajuste fino.
4. **Filtra** com detrend, Butterworth passa-baixa e/ou passa-alta (fase zero) e ordem configurável,
   com o mesmo filtro ou filtros separados para COP e celular.
5. **Plota** AP × tempo, ML × tempo e AP × ML com a elipse de 95%, para o COP e para o celular.
6. **Calcula** as variáveis (Farias et al., *Lepr Rev* 2026; Quijoux et al., 2021):
   RMS AP/ML, amplitude AP/ML, deslocamento total, velocidade média, área da elipse 95%,
   frequência mediana AP/ML e energia LF (0–0,5 Hz), MF (0,5–2 Hz) e HF (2–8 Hz) AP/ML.
7. **Exporta** um Excel com as abas Variáveis, Linha única por sujeito, Sincronização, Segmentação,
   Concordância de eixos, Parâmetros e os sinais filtrados de cada condição.

### Convenções de eixos (padrão, ajustáveis na barra lateral)
| Fonte | AP | ML | Vertical |
|---|---|---|---|
| Plataforma | COPy | COPx | Fz |
| Celular (lombar) | +Z | −X | Y |

**Girar 90°**: marque essa opção para a condição em que a pessoa ficou de lado na plataforma (ex.: tandem).
Com ela marcada, AP = COPx e ML = −COPy. O app avisa quando os eixos do COP parecem girados em relação aos
do celular. A aba *Concordancia_eixos* do Excel traz as correlações usadas nessa detecção.

### Vários arquivos de plataforma
Se você carregar um arquivo por condição, o que tiver o salto é sincronizado pelo salto e os outros por
correlação cruzada COP × aceleração. A busca respeita a ordem dos arquivos, e o r de cada correlação é
mostrado no app.

## Rodar localmente

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Publicar no Streamlit Community Cloud

1. Crie um repositório no GitHub e envie estes arquivos:
   ```bash
   git init
   git add .
   git commit -m "App de equilíbrio COP x COM"
   git branch -M main
   git remote add origin https://github.com/SEU_USUARIO/equilibrio-cop-com.git
   git push -u origin main
   ```
2. Em <https://share.streamlit.io> → **Create app** → escolha o repositório, branch `main`, arquivo `app.py`.
3. Para restringir o acesso, deixe o repositório privado e/ou configure os viewers do app.

> ⚠️ **Não envie dados de participantes ao GitHub.** O `.gitignore` já ignora `*.txt`, `*.csv`, `*.xlsx`
> e as pastas `dados/` e `resultados*/`.

## Linha de comando (em lote, sem interface)

```bash
# protocolo real: 1 arquivo da plataforma
python analise_equilibrio.py --modo unico --celular acel.txt --plataforma plataforma.txt \
    --ordem OF JOA OA TOF JOF TOA --sujeito Fulano [--rotacao_90 TOA TOF]

# um arquivo por condição
python analise_equilibrio.py --modo separado --celular acel.csv \
    --plataforma PJOA.txt PJOF.txt PSOA.txt PSOF.txt --ordem JOA JOF SOA SOF --sujeito Fulano
```

## Estrutura

```
app.py                 # interface Streamlit
balance_core.py        # funções de cálculo (leitura, sincronização, segmentação, filtro, variáveis)
analise_equilibrio.py  # versão de linha de comando (gera PNG + Excel)
requirements.txt
.streamlit/config.toml
```

## Observações
- Unidade do COP assumida: cm (ajustável).
- O total path e as energias usam definições padrão (soma das distâncias ponto a ponto e integral da PSD
  de Welch), então a escala pode diferir da usada em outros trabalhos.
