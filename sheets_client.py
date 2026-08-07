"""Google Sheets — wellness e composicao corporal.

Sem Streamlit: autentica com a Service Account no GCP_SERVICE_ACCOUNT
(JSON completo numa variavel de ambiente, como no Railway).

Dois sheets, como no dashboard original:
  WELLNESS_URL  aba "Respostas ao formulario 1"  -> HRV, RHR, sono, stress...
  FOOD_URL      aba "Consolidado_Comida"          -> calorias, macros, peso, BF
"""

import os
import json
import re
from datetime import datetime, timedelta

WELLNESS_URL = os.getenv("WELLNESS_URL",
    "https://docs.google.com/spreadsheets/d/"
    "10pefcY6VI4Z45M8Y69D6JxIoqOkjzSlSpV1PMLXoYlI/edit")
FOOD_URL = os.getenv("FOOD_URL", WELLNESS_URL)
WELLNESS_ABA = os.getenv("WELLNESS_ABA", "Respostas ao formulário 1")
FOOD_ABA = os.getenv("FOOD_ABA", "Consolidado_Comida")

SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly',
          'https://www.googleapis.com/auth/drive.readonly']

# Igual ao MAPA_WELLNESS do dashboard: varios nomes possiveis por variavel
MAPA_WELLNESS = {
    'hrv':           ['HRV', 'hrv', 'RMSSD', 'rmssd', 'Heart Rate Variability'],
    'rhr':           ['HRR', 'RHR', 'rhr', 'RestingHR', 'Resting HR'],
    'sleep_hours':   ['Horas de Sono', 'Sleep', 'sleep', 'Hours Sleep'],
    'sleep_quality': ['Sono Qualidade', 'Sono_Qualidade', 'Sleep Quality',
                      'Qualidade do Sono', 'Qualidade Sono', 'sleep_quality',
                      'Como foi o seu sono?', 'Sono', 'Quality Sleep'],
    'stress':        ['Stress Do dia', 'Stress', 'stress'],
    'fatiga':        ['Cansaço/Vontade de Treinar', 'Fatiga', 'Fadiga'],
    'humor':         ['Humor', 'humor', 'Mood'],
    'soreness':      ['Cansaço Muscular Geral', 'Muscle Soreness', 'Soreness'],
    'peso':          ['Peso', 'Weight'],
    'fat':           ['FAT', 'Fat', 'Gordura'],
    'hf_power':      ['HF Power', 'HF_Power', 'hf_power', 'HF', 'hf',
                      'hrv_hf', 'HRV_HF', 'hf power'],
}

# Escalas 1-5 em que 5 = melhor. Guardado para os graficos saberem a direccao.
ESCALA_1A5 = ['sleep_quality', 'stress', 'fatiga', 'humor', 'soreness']

MAPA_CORPORAL = ['Peso', 'BF', 'Calorias', 'Carb', 'Fat', 'Ptn',
                 'Carb_perc', 'Fat_perc', 'Ptn_perc', 'Net']

# Limites fisiologicos, iguais aos do dashboard
RANGES = {'Peso': (30, 200), 'BF': (3, 50), 'Calorias': (500, 6000),
          'Carb': (0, 800), 'Fat': (0, 400), 'Ptn': (0, 400),
          'Net': (-2000, 4000), 'hrv': (5, 250), 'rhr': (25, 120),
          'sleep_hours': (0, 16), 'peso': (30, 200), 'fat': (3, 50)}

_gc = None
_erro_auth = None


def _cliente():
    """Autentica com a Service Account. None se nao der."""
    global _gc, _erro_auth
    if _gc is not None:
        return _gc
    bruto = os.getenv("GCP_SERVICE_ACCOUNT", "").strip()
    if not bruto:
        _erro_auth = "GCP_SERVICE_ACCOUNT nao definida"
        return None
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        info = json.loads(bruto)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        _gc = gspread.authorize(creds)
        print("Google Sheets: autenticado")
        return _gc
    except Exception as e:
        _erro_auth = f"{type(e).__name__}: {e}"
        print(f"Google Sheets indisponivel: {_erro_auth}")
        return None


def disponivel():
    return _cliente() is not None


def erro_auth():
    _cliente()
    return _erro_auth


# ── conversores ───────────────────────────────────────────────────────────

def br_float(v):
    """Numero em formato PT-BR: '1.234,56' -> 1234.56. None se nao der."""
    if v is None or v == '':
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace('%', '').replace(' ', '')
    if not s or s.lower() in ('nan', 'none', '-', '#n/a', '#div/0!'):
        return None
    if ',' in s and '.' in s:
        s = s.replace('.', '').replace(',', '.')      # 1.234,56 -> 1234.56
    elif ',' in s:
        s = s.replace(',', '.')                        # 1234,56  -> 1234.56
    elif '.' in s:
        # '2.400' e 2400 (separador de milhares), '72.5' e 72.5.
        # Regra: pontos seguidos de exactamente 3 digitos, e mais do que um
        # grupo, sao separadores de milhares.
        partes = s.lstrip('-+').split('.')
        if len(partes) > 1 and all(len(p) == 3 for p in partes[1:]):
            s = s.replace('.', '')
    try:
        return float(s)
    except ValueError:
        return None


def parse_date(v):
    """Varios formatos -> YYYY-MM-DD. None se nao der."""
    if v is None or v == '':
        return None
    s = str(v).strip()
    if not s or s.lower() in ('nan', 'none'):
        return None
    s = s.split(' ')[0] if ' ' in s and len(s) > 10 else s
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(s[:10], fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', s)
    return m.group(0) if m else None


def detectar_col(cabecalho, nomes):
    """Primeira coluna que bate com um dos nomes (sem dar erro por acentos)."""
    def limpa(x):
        return re.sub(r'[^a-z0-9]', '', str(x).lower())
    limpos = {limpa(c): c for c in cabecalho}
    for n in nomes:
        k = limpa(n)
        if k in limpos:
            return limpos[k]
    for n in nomes:                       # correspondencia parcial
        k = limpa(n)
        for lc, orig in limpos.items():
            if k and (k in lc or lc in k):
                return orig
    return None


def _dentro(col, v):
    if v is None:
        return None
    lo, hi = RANGES.get(col, (None, None))
    if lo is not None and not (lo <= v <= hi):
        return None
    return v


def _ler_aba(url, aba):
    gc = _cliente()
    if gc is None:
        return None, _erro_auth
    try:
        ws = gc.open_by_url(url).worksheet(aba)
        return ws.get_all_values(), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


# ── wellness ──────────────────────────────────────────────────────────────

def carregar_wellness(dias=None):
    """HRV, RHR, sono, stress, fadiga, humor, dores, peso, gordura.

    Devolve (linhas, erro). Uma linha por dia, ordenada.
    """
    vals, err = _ler_aba(WELLNESS_URL, WELLNESS_ABA)
    if err:
        return [], err
    if not vals or len(vals) < 2:
        return [], "aba vazia"

    cab = vals[0]
    col_data = detectar_col(cab, ['Data', 'data', 'Date', 'Carimbo de data/hora'])
    if not col_data:
        return [], f"sem coluna de data (colunas: {cab[:8]})"

    idx = {c: i for i, c in enumerate(cab)}
    mapa = {}
    for var, nomes in MAPA_WELLNESS.items():
        c = detectar_col(cab, nomes)
        if c:
            mapa[var] = idx[c]

    limite = ((datetime.now() - timedelta(days=dias)).strftime('%Y-%m-%d')
              if dias else None)
    por_dia = {}
    for linha in vals[1:]:
        if len(linha) <= idx[col_data]:
            continue
        d = parse_date(linha[idx[col_data]])
        if not d or (limite and d < limite):
            continue
        r = {'date': d}
        for var, i in mapa.items():
            if i < len(linha):
                r[var] = _dentro(var, br_float(linha[i]))
        por_dia[d] = r        # se houver duas respostas no mesmo dia, fica a ultima

    return [por_dia[d] for d in sorted(por_dia)], None


# ── composicao corporal e nutricao ────────────────────────────────────────

def carregar_corporal(dias=None):
    """Calorias, macros em gramas, peso e percentagem de gordura."""
    vals, err = _ler_aba(FOOD_URL, FOOD_ABA)
    if err:
        return [], err
    if not vals or len(vals) < 2:
        return [], "aba vazia"

    cab = [c.strip() for c in vals[0]]
    col_data = detectar_col(cab, ['Data', 'data', 'Date'])
    if not col_data:
        return [], f"sem coluna de data (colunas: {cab[:8]})"

    idx = {c: i for i, c in enumerate(cab)}
    mapa = {c: idx[c] for c in MAPA_CORPORAL if c in idx}
    hoje = datetime.now().strftime('%Y-%m-%d')
    limite = ((datetime.now() - timedelta(days=dias)).strftime('%Y-%m-%d')
              if dias else None)

    por_dia = {}
    for linha in vals[1:]:
        if len(linha) <= idx[col_data]:
            continue
        d = parse_date(linha[idx[col_data]])
        if not d or d > hoje or (limite and d < limite):
            continue           # nada de datas futuras
        r = {'date': d}
        tem = False
        for c, i in mapa.items():
            if i < len(linha):
                v = _dentro(c, br_float(linha[i]))
                r[c.lower()] = v
                if v is not None:
                    tem = True
        if tem:                # ignorar linhas so com data
            por_dia[d] = r

    return [por_dia[d] for d in sorted(por_dia)], None


def diagnostico():
    """Estado da ligacao e cabecalhos reconhecidos em cada sheet."""
    out = {'autenticado': disponivel(), 'erro_auth': erro_auth(),
           'wellness_url': WELLNESS_URL[:60] + '...',
           'abas': {'wellness': WELLNESS_ABA, 'corporal': FOOD_ABA}}
    if not out['autenticado']:
        return out

    for nome, url, aba, mapa in [
            ('wellness', WELLNESS_URL, WELLNESS_ABA, MAPA_WELLNESS),
            ('corporal', FOOD_URL, FOOD_ABA, {c: [c] for c in MAPA_CORPORAL})]:
        vals, err = _ler_aba(url, aba)
        if err:
            out[nome] = {'ok': False, 'erro': err}
            continue
        cab = [c.strip() for c in (vals[0] if vals else [])]
        reconhecidas, em_falta = {}, []
        for var, nomes in mapa.items():
            c = detectar_col(cab, nomes)
            if c:
                reconhecidas[var] = c
            else:
                em_falta.append(var)
        out[nome] = {'ok': True, 'linhas': max(0, len(vals) - 1),
                     'colunas_no_sheet': cab,
                     'reconhecidas': reconhecidas, 'em_falta': em_falta}
    return out
