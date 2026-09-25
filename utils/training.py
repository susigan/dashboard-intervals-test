# utils/training.py
# Training Engine — ATHELTICA
#
# Recebe um contexto já montado pelo chamador e devolve opções de treino
# fisiologicamente plausíveis, derivadas da Tabela_Mestre_V5 e dos dados
# individuais do atleta.
#
# REGRAS ABSOLUTAS (não alterar):
#   - Não acessa DB nem API
#   - Não diagnostica limitadores
#   - Não cria nova fisiologia
#   - Não cria thresholds universais
#   - Não inventa dados ausentes
#   - Não escolhe vencedor entre limitadores
#   - Não cria scores
#   - Reutiliza _VST_PADRAO_PARA_INTERVENCAO e para_limitador() existentes
#
# Funções exportadas (8):
#   carregar_tabela, calcular_zonas, mapear_achados, identificar_limitadores,
#   filtrar_tabela, buscar_sessoes_comparaveis, calcular_referencias_individuais,
#   derivar_estado_progressao, montar_saida
#   (montar_saida chama internamente as funções auxiliares de dose/distância/
#    monitoramento/progressão — não são exportadas)

from __future__ import annotations

import math
import os
import sys
from typing import Any

import pandas as pd

# ── Importar mapeamentos canónicos já existentes no projecto ──────────────
_HERE = os.path.dirname(__file__)
sys.path.insert(0, _HERE)

from intervencoes import para_limitador  # noqa: E402 — necessário

# _VST_PADRAO_PARA_INTERVENCAO importado de vst_verificacao para evitar
# duplicação. Se o import falhar (contexto sem o módulo), usa cópia local
# idêntica ao ficheiro original.
try:
    sys.path.insert(0, os.path.dirname(_HERE))
    from vst_verificacao import _VST_PADRAO_PARA_INTERVENCAO  # noqa: E402
except Exception:
    _VST_PADRAO_PARA_INTERVENCAO = {
        'PADRÃO CARDIORRESPIRATÓRIO PREDOMINANTE': 'entrega',
        'PADRÃO PERIFÉRICO PREDOMINANTE': 'utilizacao',
        'RESPOSTA MULTISSISTÊMICA': None,
        'PADRÃO MISTO': None,
        'RESPOSTAS DISSOCIADAS': None,
        'EVIDÊNCIA INSUFICIENTE': None,
    }

# ── Mapeamento rede_causal.sistema → chave canónica ──────────────────────
# Equivalente ao SISTEMA_PARA_CHAVE em tab_moxy.py — mantido aqui para
# que training.py seja autónomo sem depender do módulo de frontend.
_SISTEMA_PARA_CHAVE: dict[str, str] = {
    'cardiaco': 'entrega',
    'cardíaco': 'entrega',
    'periferico': 'utilizacao',
    'periférico': 'utilizacao',
    'respiratorio': 'respiratorio',
    'respiratório': 'respiratorio',
    # 'autonomico' não mapeia para nenhuma chave das intervenções actuais
}

# ── Mapeamento texto limiter da tabela → chave canónica ──────────────────
# Permite que filtrar_tabela compare chaves, nunca texto livre.
_LIMITER_TEXTO_PARA_CHAVE: dict[str, str] = {
    'Limitação de entrega (cardíaco)': 'entrega',
    'Limitação de utilização (oxidativa muscular)': 'utilizacao',
    'Limitação respiratória': 'respiratorio',
    'Acúmulo de fadiga / recuperação limitada': 'fadiga',
    'Limitação mecânica / coordenação': 'mecanico',
}

# ── Chaves limiar — mecanismos específicos exigidos para cada regra ───────
# A regra limiar só entra em candidatas se achados_mapeados contiver
# pelo menos um mecanismo da lista correspondente.
_LIMIAR_MECANISMO_EXIGIDO: dict[str, list[str]] = {
    'CO2-Z3-01': [
        'co2', 'drive ventilatório', 'drive ventilatorio',
        'tolerância co2', 'tolerancia co2',
    ],
    'PERF-Z3-01': [
        'repetição de esforços', 'repeticao de esforcos',
        'rsabateria', 'rsa', 'fadiga de repetição', 'repeated effort',
    ],
    'FR-Z3-01': [
        'acúmulo de fadiga', 'acumulo de fadiga',
        'recuperação limitada z3', 'recovery comprometido alta intensidade',
    ],
}

# ─────────────────────────────────────────────────────────────────────────
# 1. carregar_tabela
# ─────────────────────────────────────────────────────────────────────────

def carregar_tabela(caminho_xlsx: str) -> list[dict]:
    """Lê Tabela_Mestre_V5.xlsx e devolve lista de dicts (uma por regra).

    Não filtra, não interpreta, não calcula. Adiciona o campo
    'limiter_chave' com a chave canónica derivada de _LIMITER_TEXTO_PARA_CHAVE
    para evitar comparações de texto livre em filtrar_tabela.
    """
    df = pd.read_excel(caminho_xlsx, sheet_name='Tabela_Mestre')
    regras: list[dict] = []
    for _, row in df.iterrows():
        r: dict[str, Any] = {}
        for col in df.columns:
            val = row[col]
            # NaN → None para simplificar verificações posteriores
            if isinstance(val, float) and math.isnan(val):
                val = None
            r[col] = val
        # Chave canónica derivada uma vez na carga
        r['limiter_chave'] = _LIMITER_TEXTO_PARA_CHAVE.get(
            str(r.get('limiter', '') or '').strip(), None
        )
        regras.append(r)
    return regras


# ─────────────────────────────────────────────────────────────────────────
# 2. calcular_zonas
# ─────────────────────────────────────────────────────────────────────────

def calcular_zonas(
    bp1_w: float | None,
    bp2_w: float | None,
) -> dict | None:
    """Define limites de Z1/Z2/Z3 a partir de BP1 e BP2 da sessão actual.

    Retorna None se qualquer um for None.
    CP não é aceite como substituto.
    """
    if bp1_w is None or bp2_w is None:
        return None
    return {
        'Z1_max': bp1_w,
        'Z2_min': bp1_w,
        'Z2_max': bp2_w,
        'Z3_min': bp2_w,
    }


def _zona_de_watts(watts: float, zonas: dict) -> str | None:
    """Classifica um valor de potência na zona correspondente."""
    if watts < zonas['Z1_max']:
        return 'Z1'
    if zonas['Z2_min'] <= watts < zonas['Z2_max']:
        return 'Z2'
    if watts >= zonas['Z3_min']:
        return 'Z3'
    return None


# ─────────────────────────────────────────────────────────────────────────
# 3. mapear_achados
# ─────────────────────────────────────────────────────────────────────────

def mapear_achados(contexto: dict) -> dict:
    """Percorre as quatro fontes e produz achados_mapeados + divergencias.

    Usa exclusivamente o que já existe nos dados — nunca cria análise nova.
    Plausibilidade: 'evidenciado' | 'sugerido' | 'insuficiente'
    Subtipo respiratório: só quando fonte já o indica explicitamente.
    ME: só quando evidência explícita — nunca por ausência de outros.
    """
    achados: list[dict] = []
    dados_ausentes: list[str] = []

    # ── Fonte 1: Rede Causal ──────────────────────────────────────────────
    rc = (contexto.get('achados') or {}).get('rede_causal') or {}
    if rc.get('disponivel') and rc.get('sistema'):
        sistema = str(rc['sistema']).strip().lower()
        chave = _SISTEMA_PARA_CHAVE.get(sistema)
        if chave:
            nome_lim = (para_limitador(chave) or {}).get('nome') or chave
            plaus = 'evidenciado' if (rc.get('pct') or 0) >= 50 else 'sugerido'
            achados.append({
                'fonte': 'rede_causal',
                'limitador': nome_lim,
                'limitador_chave': chave,
                'mecanismo': None,  # rede não distingue subtipo respiratório
                'plausibilidade': plaus,
                'nota': None,
            })
        elif sistema == 'autonomico':
            dados_ausentes.append('rede_causal: sistema autonómico sem chave canónica')
        else:
            dados_ausentes.append(f'rede_causal: sistema "{sistema}" não mapeado')
    elif rc.get('disponivel') is False or not rc:
        dados_ausentes.append('rede_causal: indisponível')

    # ── Fonte 2: Intervenções (5-1-5) ────────────────────────────────────
    iv = (contexto.get('achados') or {}).get('intervencoes') or {}
    if iv.get('disponivel'):
        for eixo in ('us_limitador', 'pc_limitador'):
            val = iv.get(eixo)
            if val:
                r = para_limitador(str(val).strip().lower())
                if r:
                    chave_iv = _para_chave_canonica_intervencoes(str(val))
                    achados.append({
                        'fonte': 'intervencoes',
                        'limitador': r.get('nome') or val,
                        'limitador_chave': chave_iv,
                        'mecanismo': None,
                        'plausibilidade': 'sugerido',
                        'nota': None,
                    })
    elif not iv or iv.get('disponivel') is False:
        dados_ausentes.append('intervencoes: indisponível')

    # ── Fonte 3: VST ─────────────────────────────────────────────────────
    vst = (contexto.get('achados') or {}).get('vst') or {}
    if vst.get('disponivel'):
        for campo in ('limiter_bp1_padrao', 'limiter_bp2_padrao'):
            padrao = vst.get(campo)
            if padrao:
                chave_vst = _VST_PADRAO_PARA_INTERVENCAO.get(str(padrao).strip())
                # Usar somente a chave canónica — não criar segundo limitador
                # a partir do mesmo padrão (ex: CARDIORRESPIRATÓRIO → só 'entrega')
                if chave_vst:
                    nome_lim = (para_limitador(chave_vst) or {}).get('nome') or chave_vst
                    status_comp = vst.get(
                        'comparacao_bp1_status' if 'bp1' in campo
                        else 'comparacao_bp2_status'
                    )
                    plaus = _plaus_de_status_vst(status_comp)
                    achados.append({
                        'fonte': 'vst',
                        'limitador': nome_lim,
                        'limitador_chave': chave_vst,
                        'mecanismo': None,
                        'plausibilidade': plaus,
                        'nota': f'padrão VST: {padrao}',
                    })
                elif padrao not in ('EVIDÊNCIA INSUFICIENTE', None):
                    # Padrão None/multissistêmico/dissociado — registar mas sem chave
                    dados_ausentes.append(
                        f'vst: padrão "{padrao}" sem chave canónica única'
                    )
    elif not vst or vst.get('disponivel') is False:
        dados_ausentes.append('vst: indisponível')

    # ── Fonte 4: MOXY ────────────────────────────────────────────────────
    # MOXY bruto não produz um limitador directo — apenas contribui para
    # subtipo respiratório se já interpretado. Não criar análise nova.
    moxy = (contexto.get('achados') or {}).get('moxy') or {}
    if not moxy or moxy.get('disponivel') is False:
        dados_ausentes.append('moxy: indisponível ou sem dados por degrau')

    # ── Subtipo respiratório ──────────────────────────────────────────────
    # Só marcado quando a fonte já o indica explicitamente.
    # Nenhuma fonte actual produz subtipos respiratórios específicos (RD/RM/CO2).
    # Resultado: subtipo sempre None nesta versão — declarado em dados_ausentes.
    tem_resp = any(a['limitador_chave'] == 'respiratorio' for a in achados)
    if tem_resp:
        dados_ausentes.append('subtipo respiratório não determinado — '
                              'nenhuma fonte disponível distingue RD/RM/CO2')

    # ── Divergências entre fontes ─────────────────────────────────────────
    divergencias = _detectar_divergencias(achados)

    return {
        'achados_mapeados': achados,
        'divergencias': divergencias,
        'dados_ausentes': dados_ausentes,
    }


def _para_chave_canonica_intervencoes(us_val: str) -> str | None:
    """Converte us_limitador para chave canónica via para_limitador."""
    r = para_limitador(us_val.strip().lower())
    if not r:
        return None
    nome = r.get('nome') or ''
    return _LIMITER_TEXTO_PARA_CHAVE.get(nome)


def _plaus_de_status_vst(status: str | None) -> str:
    if not status:
        return 'insuficiente'
    s = str(status).upper()
    if 'CONSISTENTE' in s and 'PARCIALMENTE' not in s:
        return 'evidenciado'
    if 'PARCIALMENTE' in s:
        return 'sugerido'
    return 'insuficiente'


def _detectar_divergencias(achados: list[dict]) -> list[dict]:
    divergencias: list[dict] = []
    chaves = [a['limitador_chave'] for a in achados if a['limitador_chave']]
    chaves_unicas = set(chaves)
    if len(chaves_unicas) > 1:
        # Há achados com chaves distintas — registar pares divergentes
        fontes_por_chave: dict[str, list[str]] = {}
        for a in achados:
            k = a['limitador_chave']
            if k:
                fontes_por_chave.setdefault(k, []).append(a['fonte'])
        chaves_lista = sorted(chaves_unicas)
        for i, ka in enumerate(chaves_lista):
            for kb in chaves_lista[i + 1:]:
                divergencias.append({
                    'chave_a': ka,
                    'fontes_a': fontes_por_chave.get(ka, []),
                    'chave_b': kb,
                    'fontes_b': fontes_por_chave.get(kb, []),
                    'nota': (f'Fontes indicam limitadores distintos: '
                             f'"{ka}" e "{kb}" — preservados ambos'),
                })
    return divergencias


# ─────────────────────────────────────────────────────────────────────────
# 4. identificar_limitadores
# ─────────────────────────────────────────────────────────────────────────

def identificar_limitadores(achados_mapeados: list[dict]) -> list[dict]:
    """Agrega achados por chave canónica; preserva múltiplos sem vencedor."""
    por_chave: dict[str, dict] = {}
    for a in achados_mapeados:
        chave = a.get('limitador_chave')
        if not chave:
            continue
        if chave not in por_chave:
            por_chave[chave] = {
                'limitador_chave': chave,
                'limitador_nome': a['limitador'],
                'mecanismo': a.get('mecanismo'),
                'plausibilidade': a['plausibilidade'],
                'fontes': [a['fonte']],
                'notas': [a['nota']] if a.get('nota') else [],
            }
        else:
            entrada = por_chave[chave]
            # Actualizar plausibilidade para a mais forte
            entrada['plausibilidade'] = _plaus_mais_forte(
                entrada['plausibilidade'], a['plausibilidade']
            )
            if a['fonte'] not in entrada['fontes']:
                entrada['fontes'].append(a['fonte'])
            if a.get('nota') and a['nota'] not in entrada['notas']:
                entrada['notas'].append(a['nota'])
            # Mecanismo: preservar se já havia, ou adoptar se surgir
            if not entrada['mecanismo'] and a.get('mecanismo'):
                entrada['mecanismo'] = a['mecanismo']
    return list(por_chave.values())


def _plaus_mais_forte(a: str, b: str) -> str:
    ordem = {'evidenciado': 2, 'sugerido': 1, 'insuficiente': 0}
    return a if ordem.get(a, 0) >= ordem.get(b, 0) else b


# ─────────────────────────────────────────────────────────────────────────
# 5. filtrar_tabela
# ─────────────────────────────────────────────────────────────────────────

def filtrar_tabela(
    limitadores: list[dict],
    modalidade: str,
    zonas: dict | None,
    tabela: list[dict],
    achados_mapeados: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Aplica Nível 1 (exclusão obrigatória) e Nível 2 (relevance).

    Retorna (candidatas, excluidas_com_motivo).
    """
    candidatas: list[dict] = []
    excluidas: list[dict] = []

    chaves_limitador = {lim['limitador_chave'] for lim in limitadores}
    # plausibilidade por chave (para Nível 2)
    plaus_por_chave = {
        lim['limitador_chave']: lim['plausibilidade'] for lim in limitadores
    }
    # mecanismos presentes nos achados (para regras limiar)
    mecanismos_achados = [
        str(a.get('mecanismo') or '').strip().lower()
        for a in achados_mapeados
        if a.get('mecanismo')
    ]

    for regra in tabela:
        rule_id = regra.get('rule_id', '?')

        # ── Nível 1: Modalidade ───────────────────────────────────────────
        modalidades_regra = [
            m.strip() for m in str(regra.get('modality') or '').split('/')
        ]
        if modalidade not in modalidades_regra:
            excluidas.append({
                'rule_id': rule_id,
                'motivo': f'modalidade "{modalidade}" não está em {modalidades_regra}',
            })
            continue

        # ── Nível 1: Zonas disponíveis ────────────────────────────────────
        if zonas is None:
            excluidas.append({
                'rule_id': rule_id,
                'motivo': 'BP1/BP2 indisponíveis para a modalidade — zona não determinável',
            })
            continue

        # ── Nível 1: Limitador ────────────────────────────────────────────
        chave_regra = regra.get('limiter_chave')
        if chave_regra not in chaves_limitador:
            excluidas.append({
                'rule_id': rule_id,
                'motivo': (f'limitador "{chave_regra}" não identificado nos achados '
                           f'({sorted(chaves_limitador)})'),
            })
            continue

        # ── Nível 1: exclusion_rule (plausibilidade insuficiente) ──────────
        plaus = plaus_por_chave.get(chave_regra, 'insuficiente')
        if plaus == 'insuficiente':
            excluidas.append({
                'rule_id': rule_id,
                'motivo': 'plausibilidade do limitador é insuficiente para esta regra',
            })
            continue

        # ── Nível 2: relevance ────────────────────────────────────────────
        relevance = str(regra.get('relevance') or '').strip().lower()

        if relevance == 'limiar':
            # Só entra com evidência explícita e específica do mecanismo
            exigidos = _LIMIAR_MECANISMO_EXIGIDO.get(rule_id, [])
            tem_evidencia = any(
                mec in ' '.join(mecanismos_achados)
                for mec in exigidos
            )
            if not tem_evidencia:
                excluidas.append({
                    'rule_id': rule_id,
                    'motivo': (
                        f'relevance=limiar: sem evidência específica do mecanismo '
                        f'"{regra.get("mechanism_target")}" nos achados disponíveis'
                    ),
                })
                continue

        # Para limitação respiratória: só incluir regras de subtipo específico
        # se o subtipo estiver nos achados. Subtipo None → excluir RD/RM/CO2
        # como 'principal'; só manter como 'possível' se não exigir subtipo.
        if chave_regra == 'respiratorio':
            subtipo_achado = next(
                (a.get('mecanismo') for a in achados_mapeados
                 if a.get('limitador_chave') == 'respiratorio'
                 and a.get('mecanismo')),
                None
            )
            if subtipo_achado is None:
                # Subtipo não determinado — demover principal → possível
                # (nunca excluir completamente — declara no resultado)
                if relevance == 'principal':
                    relevance = 'possível'

        # Regra passou — adicionar com relevance efectivo
        entrada = dict(regra)
        entrada['_relevance_efectivo'] = relevance
        candidatas.append(entrada)

    return candidatas, excluidas


# ─────────────────────────────────────────────────────────────────────────
# 6. buscar_sessoes_comparaveis
# ─────────────────────────────────────────────────────────────────────────

def buscar_sessoes_comparaveis(
    historico: list[dict],
    modalidade: str,
    zona_alvo: str,
) -> list[dict]:
    """Filtra o histórico por modalidade e zona.

    Usa BP1/BP2 da própria sessão histórica para classificar a zona.
    Se a sessão não tiver BP próprios: zona indeterminada → não incluída.
    Não usa BP da sessão actual para classificar sessões históricas.
    """
    comparaveis: list[dict] = []
    for sessao in historico:
        if str(sessao.get('modalidade') or '') != modalidade:
            continue
        # Usar BP da própria sessão histórica
        bp1 = sessao.get('bp1_w')
        bp2 = sessao.get('bp2_w')
        if bp1 is None or bp2 is None:
            # Zona indeterminável para esta sessão histórica — não incluir
            continue
        zonas_hist = calcular_zonas(bp1, bp2)
        if zonas_hist is None:
            continue
        # Verificar se a sessão tem dados na zona_alvo
        hr_zona = (sessao.get('hr_medio_por_zona') or {}).get(zona_alvo)
        rf_zona = (sessao.get('rf_medio_por_zona') or {}).get(zona_alvo)
        pot_zona = (sessao.get('potencia_por_zona') or {}).get(zona_alvo)
        # A sessão é comparável se tiver dados registados naquela zona
        # (pelo menos potência ou HR)
        if hr_zona is None and pot_zona is None:
            # Sem dados na zona alvo — não incluir
            continue
        comparaveis.append(sessao)

    # Ordenar por data decrescente (mais recente primeiro)
    comparaveis.sort(
        key=lambda s: str(s.get('data') or ''), reverse=True
    )
    return comparaveis


# ─────────────────────────────────────────────────────────────────────────
# 7. calcular_referencias_individuais
# ─────────────────────────────────────────────────────────────────────────

def calcular_referencias_individuais(
    sessoes: list[dict],
    regra: dict,
    zona: str,
) -> dict:
    """Calcula referências individuais conforme runtime_value_type da regra.

    Método controlado por threshold_calculation + runtime_value_type.
    Nunca usa valor universal. SmO2 nunca isolada.
    """
    rv_type = str(regra.get('runtime_value_type') or '').strip().lower()
    resultado: dict[str, Any] = {}
    dados_ausentes_ref: list[str] = []

    if not sessoes:
        # Zero sessões comparáveis — tudo indisponível
        for metrica in ('HR', 'RF', 'SmO2', 'potencia'):
            resultado[metrica] = {'valor': None, 'metodo': rv_type, 'disponivel': False}
        resultado['_dados_ausentes'] = [
            f'zero sessões comparáveis para zona {zona}'
        ]
        return resultado

    # ── Extrair valores por métrica ───────────────────────────────────────
    def _vals(campo_zona: str) -> list[float]:
        vals = []
        for s in sessoes:
            d = s.get(campo_zona) or {}
            v = d.get(zona)
            if v is not None:
                try:
                    vals.append(float(v))
                except (TypeError, ValueError):
                    pass
        return vals

    hr_vals = _vals('hr_medio_por_zona')
    rf_vals = _vals('rf_medio_por_zona')
    smo2_vals = _vals('smo2_por_zona')
    rpe_vals = _vals('rpe_por_zona')
    pot_vals = _vals('potencia_por_zona')

    # ── Aplicar método conforme runtime_value_type ────────────────────────
    # O campo runtime_value_type da tabela descreve o tipo de cálculo esperado.
    # 'range' → min/max; 'max' → tecto; 'trend' → qualitativo
    # Combinações: 'range/max', 'range/trend', etc.
    tipos = set(p.strip() for p in rv_type.split('/'))

    def _calcular_metrica(vals: list[float], nome: str) -> dict:
        if not vals:
            dados_ausentes_ref.append(f'{nome} sem dados em {zona}')
            return {'valor': None, 'metodo': rv_type, 'disponivel': False}
        out: dict[str, Any] = {'metodo': rv_type, 'disponivel': True}
        if 'range' in tipos:
            out['min'] = round(min(vals), 1)
            out['max'] = round(max(vals), 1)
        if 'max' in tipos:
            out['teto'] = round(max(vals), 1)
        if 'trend' in tipos:
            # Tendência qualitativa — media das sessões por data
            if len(vals) >= 2:
                primeira_metade = vals[len(vals)//2:]  # mais antigas (desc order)
                segunda_metade = vals[:len(vals)//2]   # mais recentes
                med_antiga = sum(primeira_metade) / len(primeira_metade)
                med_recente = sum(segunda_metade) / len(segunda_metade)
                delta = med_recente - med_antiga
                if abs(delta) < 2:
                    out['trend'] = 'estável'
                elif delta > 0:
                    out['trend'] = 'subida'
                else:
                    out['trend'] = 'queda'
            else:
                out['trend'] = 'indeterminado (sessão única)'
        if 'recovery' in tipos:
            out['recovery_nota'] = 'verificar retorno ao basal nas sessões comparáveis'
        return out

    resultado['HR'] = _calcular_metrica(hr_vals, 'HR')
    resultado['RF'] = _calcular_metrica(rf_vals, 'RF')
    resultado['potencia'] = _calcular_metrica(pot_vals, 'potencia')
    resultado['RPE_faixa_tabela'] = {
        'valor': regra.get('expected_RPE_work'),
        'metodo': 'faixa da tabela (não individual)',
        'disponivel': True,
    }

    # SmO2: só incluída com contexto multimétrico
    if smo2_vals and (hr_vals or rf_vals or rpe_vals):
        resultado['SmO2'] = _calcular_metrica(smo2_vals, 'SmO2')
        resultado['SmO2']['contexto'] = (
            'sempre interpretar junto de HR, RF, RPE e potência'
        )
    else:
        resultado['SmO2'] = {
            'valor': None,
            'metodo': rv_type,
            'disponivel': False,
            'nota': 'SmO2 indisponível ou sem contexto multimétrico suficiente',
        }
        if smo2_vals:
            dados_ausentes_ref.append(
                'SmO2 disponível mas sem HR/RF/RPE para contextualizar'
            )

    if dados_ausentes_ref:
        resultado['_dados_ausentes'] = dados_ausentes_ref
    return resultado


# ─────────────────────────────────────────────────────────────────────────
# 8. derivar_estado_progressao
# ─────────────────────────────────────────────────────────────────────────

def derivar_estado_progressao(
    sessoes: list[dict],
    regra: dict,
) -> dict:
    """Determina estado de progressão a partir do histórico.

    resultado='success' NÃO implica progression_gate atingido.
    Gate só avaliável se dados_por_bloco.tem_dados_por_bloco = True.
    """
    progression_gate_texto = str(regra.get('progression_gate') or '').strip()
    regression_gate_texto = str(regra.get('regression_gate') or '').strip()
    progression_rule = str(regra.get('progression_rule') or '').strip()
    work_range = str(regra.get('work_range') or '').strip()
    progression_order = str(regra.get('progression_order') or '').strip()

    if not sessoes:
        return {
            'ponto_de_partida': _extremo_conservador(work_range),
            'ponto_de_partida_fonte': 'conservador',
            'estado_derivado': None,
            'nota': (
                'zero sessões comparáveis — iniciar pelo extremo conservador '
                f'da faixa "{work_range}"'
            ),
            'progression_gate': progression_gate_texto,
            'regression_gate': regression_gate_texto,
            'progression_rule': progression_rule,
            'progression_order': progression_order,
        }

    ultima_dose = None
    ultima_com_success = None
    tem_failure = False
    gate_atingido = False
    gate_avaliavel = False

    for sessao in sessoes:  # mais recente primeiro
        dose = sessao.get('dose_executada')
        resultado = str(sessao.get('resultado') or '').lower()
        dados_bloco = sessao.get('dados_por_bloco') or {}
        tem_dados_bloco = bool(dados_bloco.get('tem_dados_por_bloco', False))

        if dose and ultima_dose is None:
            ultima_dose = dose

        if resultado == 'failure':
            tem_failure = True
            break  # regressão: parar na primeira failure mais recente

        if resultado == 'success' and ultima_com_success is None:
            ultima_com_success = sessao
            # Avaliar se o gate era avaliável nesta sessão
            if tem_dados_bloco:
                gate_avaliavel = True
                # O texto do gate é qualitativo — não podemos avaliá-lo
                # automaticamente sem nova análise fisiológica.
                # Por conservadorismo: gate_atingido = False aqui.
                # O chamador pode sobrescrever este campo com avaliação manual.
                gate_atingido = False
            # Se não tem dados por bloco: gate não avaliável
            break

    # Determinar estado
    if tem_failure:
        return {
            'ponto_de_partida': ultima_dose or _extremo_conservador(work_range),
            'ponto_de_partida_fonte': 'histórico',
            'estado_derivado': 'regredir',
            'nota': 'última sessão comparável com resultado=failure — '
                    'manter ou regredir dose',
            'progression_gate': progression_gate_texto,
            'regression_gate': regression_gate_texto,
            'progression_rule': progression_rule,
            'progression_order': progression_order,
        }

    if ultima_com_success and gate_avaliavel:
        return {
            'ponto_de_partida': ultima_dose or _extremo_conservador(work_range),
            'ponto_de_partida_fonte': 'histórico',
            'estado_derivado': 'manter',
            'nota': (
                'progression_gate não avaliável automaticamente — '
                f'gate exige: "{progression_gate_texto}". '
                'Avaliar manualmente antes de progredir.'
            ),
            'progression_gate': progression_gate_texto,
            'regression_gate': regression_gate_texto,
            'progression_rule': progression_rule,
            'progression_order': progression_order,
        }

    if ultima_com_success and not gate_avaliavel:
        return {
            'ponto_de_partida': ultima_dose or _extremo_conservador(work_range),
            'ponto_de_partida_fonte': 'histórico',
            'estado_derivado': 'manter',
            'nota': (
                'resultado=success mas dados por bloco ausentes — '
                'progression_gate indisponível'
            ),
            'progression_gate': progression_gate_texto,
            'regression_gate': regression_gate_texto,
            'progression_rule': progression_rule,
            'progression_order': progression_order,
        }

    # Sem sessão com resultado definido
    return {
        'ponto_de_partida': _extremo_conservador(work_range),
        'ponto_de_partida_fonte': 'indisponível',
        'estado_derivado': None,
        'nota': (
            f'histórico insuficiente para determinar estado — '
            f'iniciar pelo extremo conservador: "{_extremo_conservador(work_range)}"'
        ),
        'progression_gate': progression_gate_texto,
        'regression_gate': regression_gate_texto,
        'progression_rule': progression_rule,
        'progression_order': progression_order,
    }


def _extremo_conservador(work_range: str) -> str | None:
    """Extrai o extremo inferior de uma faixa textual.

    Ex: '2–4 × 8–15 min' → '2 × 8 min'
        '20–60 min' → '20 min'
    Retorna None se não interpretável.
    """
    if not work_range:
        return None
    import re
    # Formato '2–4 × 8–15 min'
    m = re.match(r'(\d+)[–\-]\d+\s*×\s*(\d+)[–\-]\d+\s*(\w+)', work_range)
    if m:
        return f'{m.group(1)} × {m.group(2)} {m.group(3)}'
    # Formato '20–60 min'
    m2 = re.match(r'(\d+)[–\-]\d+\s*(\w+)', work_range)
    if m2:
        return f'{m2.group(1)} {m2.group(2)}'
    # Não interpretável
    return None


# ─────────────────────────────────────────────────────────────────────────
# Auxiliar: calcular distância (Row/Ski/Run)
# ─────────────────────────────────────────────────────────────────────────

def _calcular_distancia(
    modalidade: str,
    zona: str,
    pace_individual: dict | None,
    sessoes_comparaveis: list[dict],
) -> dict:
    """Converte tempo em distância usando split individual.

    Bike: nunca. Row/Ski: s/500m. Run: s/1000m.
    Se pace indisponível: retorna None + nota.
    """
    if modalidade == 'Bike':
        return {'distancia_por_work': None, 'pace_referencia': None,
                'nota_distancia': 'Bike: somente tempo e potência'}

    # Tentar pace_individual do contexto
    split_s = None
    if pace_individual:
        split_s = pace_individual.get(f'{zona}_s_per_500m')

    # Fallback: split médio das sessões comparáveis
    if split_s is None:
        for s in sessoes_comparaveis:
            v = (s.get('split_medio_por_zona') or {}).get(zona)
            if v is not None:
                try:
                    split_s = float(v)
                    break
                except (TypeError, ValueError):
                    pass

    if split_s is None:
        return {
            'distancia_por_work': None,
            'pace_referencia': None,
            'nota_distancia': 'pace/split individual não disponível — mostrar somente tempo',
        }

    # Apresentar apenas o pace de referência; a distância por work
    # depende da duração específica prescrita — fornecida pelo utilizador.
    if modalidade in ('Row', 'Ski'):
        min_s = split_s
        pace_str = f'~{int(min_s)//60}:{int(min_s)%60:02d}/500m'
        return {
            'distancia_por_work': None,  # calculada quando duração for conhecida
            'pace_referencia': pace_str,
            'nota_distancia': (
                f'pace de referência individual: {pace_str} — '
                'converter quando duração do WORK for definida'
            ),
            'split_s_por_500m': round(split_s, 1),
        }

    if modalidade == 'Run':
        min_s = split_s  # aqui em s/1000m
        pace_str = f'~{int(min_s)//60}:{int(min_s)%60:02d}/km'
        return {
            'distancia_por_work': None,
            'pace_referencia': pace_str,
            'nota_distancia': (
                f'pace de referência individual: {pace_str} — '
                'converter quando duração do WORK for definida'
            ),
            'split_s_por_1000m': round(split_s, 1),
        }

    return {'distancia_por_work': None, 'pace_referencia': None,
            'nota_distancia': 'modalidade sem conversão de distância'}


# ─────────────────────────────────────────────────────────────────────────
# 9. montar_saida  (função principal de composição)
# ─────────────────────────────────────────────────────────────────────────

def montar_saida(
    contexto: dict,
    candidatas: list[dict],
    excluidas: list[dict],
    referencias: dict,     # {rule_id: dict de referências}
    estados: dict,         # {rule_id: dict de estado de progressão}
    achados_result: dict,  # output de mapear_achados
) -> dict:
    """Compõe o objecto resultado final.

    Ordena por relevance: principal → possível → limiar.
    Transparência total — nada ocultado.
    """
    modalidade = str(contexto.get('modalidade') or '').strip()
    pace_ind = contexto.get('pace_individual')

    _ORDEM_REL = {'principal': 0, 'possível': 1, 'possivel': 1, 'limiar': 2}
    candidatas_ord = sorted(
        candidatas,
        key=lambda r: _ORDEM_REL.get(
            str(r.get('_relevance_efectivo') or r.get('relevance') or '').strip().lower(),
            3
        )
    )

    opcoes: list[dict] = []
    dados_ausentes_global: list[str] = list(
        achados_result.get('dados_ausentes') or []
    )

    for regra in candidatas_ord:
        rule_id = regra.get('rule_id', '?')
        zona = str(regra.get('zone') or '').strip()
        relevance_ef = str(
            regra.get('_relevance_efectivo') or regra.get('relevance') or ''
        ).strip()

        refs = referencias.get(rule_id) or {}
        estado = estados.get(rule_id) or {}

        # Dados ausentes das referências desta regra
        ref_ausentes = refs.get('_dados_ausentes') or []
        for da in ref_ausentes:
            if da not in dados_ausentes_global:
                dados_ausentes_global.append(da)

        # ── Dose ─────────────────────────────────────────────────────────
        work_range = str(regra.get('work_range') or '').strip()
        dose_min = regra.get('dose_min')
        dose_target = regra.get('dose_target')
        dose_max = regra.get('dose_max')
        pdp = estado.get('ponto_de_partida')
        pdp_fonte = estado.get('ponto_de_partida_fonte', 'indisponível')

        dose = {
            'work_range': work_range,
            'ponto_de_partida': pdp,
            'ponto_de_partida_fonte': pdp_fonte,
            'nota_dose': estado.get('nota'),
            'dose_min': dose_min,
            'dose_target': dose_target,
            'dose_max': dose_max,
            'recovery_range': str(regra.get('recovery_range') or '').strip() or None,
        }

        # ── Distância ─────────────────────────────────────────────────────
        # Buscar sessões comparáveis para esta zona (passadas via refs)
        sessoes_zona = refs.get('_sessoes_usadas') or []
        dist_info = _calcular_distancia(modalidade, zona, pace_ind, sessoes_zona)
        dose.update(dist_info)

        # ── Referências individuais para o resultado ─────────────────────
        refs_out: dict[str, Any] = {}
        for metrica in ('HR', 'RF', 'SmO2', 'potencia', 'RPE_faixa_tabela'):
            if metrica in refs:
                refs_out[metrica] = refs[metrica]

        # ── Monitoramento ─────────────────────────────────────────────────
        monitor = {
            'primary': str(regra.get('monitor_primary') or '').strip(),
            'rules': [
                str(regra.get('monitor_rule_1') or '').strip(),
                str(regra.get('monitor_rule_2') or '').strip(),
                str(regra.get('monitor_rule_3') or '').strip(),
            ],
            'nota': ('SmO2 sempre interpretada com HR, RF, RPE e potência — '
                     'nunca isolada') if 'SmO2' in str(regra.get('monitor_primary') or '') else None,
        }

        # ── Progressão ────────────────────────────────────────────────────
        progressao = {
            'ordem': str(regra.get('progression_order') or '').strip(),
            'regra': str(regra.get('progression_rule') or '').strip(),
            'gate': estado.get('progression_gate') or str(regra.get('progression_gate') or '').strip(),
            'regression_gate': estado.get('regression_gate') or str(regra.get('regression_gate') or '').strip(),
            'estado_derivado': estado.get('estado_derivado'),
            'estado_fonte': pdp_fonte,
            'nota': estado.get('nota'),
        }

        opcoes.append({
            'rule_id': rule_id,
            'relevance': relevance_ef,
            'limitador': str(regra.get('limiter') or '').strip(),
            'limitador_chave': regra.get('limiter_chave'),
            'adaptacao': str(regra.get('adaptation_target') or '').strip(),
            'mecanismo_alvo': str(regra.get('mechanism_target') or '').strip(),
            'zona': zona,
            'modalidade': modalidade,
            'training_type': str(regra.get('training_type') or '').strip(),
            'format': str(regra.get('format') or '').strip(),
            'evidence_level': str(regra.get('evidence_level') or '').strip(),
            'expected_RPE_work': str(regra.get('expected_RPE_work') or '').strip(),
            'expected_RPE_session': str(regra.get('expected_RPE_session') or '').strip(),
            'dose': dose,
            'referencias_individuais': refs_out,
            'monitoramento': monitor,
            'success_rule': str(regra.get('success_rule') or '').strip(),
            'failure_rule': str(regra.get('failure_rule') or '').strip(),
            'work_stop_rule': str(regra.get('work_stop_rule') or '').strip(),
            'recovery_gate': str(regra.get('recovery_gate') or '').strip(),
            'progressao': progressao,
        })

    # ── Status ───────────────────────────────────────────────────────────
    if not opcoes and not candidatas:
        motivo = _motivo_sem_opcao(excluidas)
        status = 'sem_opcao_plausivel'
    else:
        motivo = None
        status = 'ok'

    return {
        'status': status,
        'achados_mapeados': achados_result.get('achados_mapeados') or [],
        'divergencias': achados_result.get('divergencias') or [],
        'opcoes': opcoes,
        'excluidas': excluidas,
        'dados_ausentes': dados_ausentes_global,
        'motivo_sem_opcao': motivo,
    }


def _motivo_sem_opcao(excluidas: list[dict]) -> str:
    if not excluidas:
        return 'nenhuma regra compatível encontrada na Tabela_Mestre para os achados actuais'
    motivos = set(e.get('motivo', '') for e in excluidas)
    return 'todas as regras excluídas — motivos: ' + '; '.join(sorted(motivos)[:3])


# ─────────────────────────────────────────────────────────────────────────
# Função de orquestração (ponto de entrada público)
# ─────────────────────────────────────────────────────────────────────────

def executar(contexto: dict, caminho_tabela: str) -> dict:
    """Ponto de entrada público — orquestra os 13 passos da especificação.

    O chamador passa o contexto já montado e o caminho para a tabela xlsx.
    Retorna o objecto resultado completo.

    Não acede a DB nem a API.
    """
    modalidade = str(contexto.get('modalidade') or '').strip()
    dados_ausentes: list[str] = []

    # ── Passo 1: dados estruturais mínimos ────────────────────────────────
    if not modalidade:
        return {
            'status': 'dados_insuficientes',
            'motivo': 'modalidade ausente no contexto',
            'achados_mapeados': [], 'divergencias': [],
            'opcoes': [], 'excluidas': [],
            'dados_ausentes': ['modalidade'],
            'motivo_sem_opcao': None,
        }

    # ── Passo 2: carregar tabela ──────────────────────────────────────────
    tabela = carregar_tabela(caminho_tabela)

    # ── Passo 3: calcular zonas ───────────────────────────────────────────
    bp1_w = contexto.get('bp1_w')
    bp2_w = contexto.get('bp2_w')
    zonas = calcular_zonas(bp1_w, bp2_w)
    if zonas is None:
        dados_ausentes.append('BP1/BP2 indisponíveis — zonas não determinadas')
        # Não é dados_insuficientes — o engine continua e exclui regras de zona

    # ── Passo 4: mapear achados ───────────────────────────────────────────
    achados_result = mapear_achados(contexto)
    dados_ausentes.extend(achados_result.get('dados_ausentes') or [])

    # ── Passo 5: identificar limitadores ─────────────────────────────────
    limitadores = identificar_limitadores(
        achados_result.get('achados_mapeados') or []
    )

    # Se nenhuma fonte disponível produziu um limitador válido:
    if not limitadores:
        return {
            'status': 'sem_opcao_plausivel',
            'motivo': 'nenhum limitador identificado nas fontes disponíveis',
            'achados_mapeados': achados_result.get('achados_mapeados') or [],
            'divergencias': achados_result.get('divergencias') or [],
            'opcoes': [], 'excluidas': [],
            'dados_ausentes': dados_ausentes,
            'motivo_sem_opcao': 'nenhum limitador identificado nas fontes disponíveis',
        }

    # ── Passo 6: filtrar tabela ───────────────────────────────────────────
    candidatas, excluidas = filtrar_tabela(
        limitadores, modalidade, zonas, tabela,
        achados_result.get('achados_mapeados') or []
    )

    # ── Passos 7–9: referências e progressão por candidata ───────────────
    referencias: dict[str, dict] = {}
    estados: dict[str, dict] = {}
    historico = list(contexto.get('historico') or [])

    for regra in candidatas:
        rule_id = regra.get('rule_id', '?')
        zona = str(regra.get('zone') or '').strip()

        # Buscar sessões comparáveis
        sessoes = buscar_sessoes_comparaveis(historico, modalidade, zona)

        # Calcular referências
        refs = calcular_referencias_individuais(sessoes, regra, zona)
        refs['_sessoes_usadas'] = sessoes  # passado para _calcular_distancia
        referencias[rule_id] = refs

        # Derivar progressão
        estados[rule_id] = derivar_estado_progressao(sessoes, regra)

    # ── Passo 10–13: montar saída ─────────────────────────────────────────
    resultado = montar_saida(
        contexto, candidatas, excluidas,
        referencias, estados, achados_result
    )

    # Consolidar dados_ausentes
    for da in dados_ausentes:
        if da not in resultado['dados_ausentes']:
            resultado['dados_ausentes'].append(da)

    return resultado
