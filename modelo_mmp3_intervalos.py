"""Intervalos das atividades como EVIDENCIA de um esforco de 3 min (modulo PURO: sem base, sem rede, sem relogio).

Fonte (auditada): o campo `interval_summary` de cada atividade (`activities.raw`, ja sincronizado com as atividades,
sem nenhuma chamada nova a API). Cada item e um GRUPO de intervalos semelhantes: `"<n>x <duracao> <valor><unidade>"`,
ex. `"1x 2m56s 189w"` (1 intervalo de 2 min 56 s a 189 W de potencia media), `"3x 3m1s 156bpm"` (frequencia
cardiaca), `"3x 4m9s 5:10"` (ritmo). Campos que EXISTEM: n de repeticoes, duracao (s), valor medio do grupo, unidade.
Campos que NAO existem aqui: inicio/fim, tipo de intervalo e id proprio do intervalo (so o id da atividade).
Intervalos individuais (`/activity/{id}/intervals`, `activity_snapshot`) exigem a API/outra base e nao sao usados.

O `interval_summary` pode vir como lista ou como texto JSON dessa lista (como as tags); ambos sao aceites.

Papel na classificacao (regra do utilizador, 2026-10-19): a tag `3min` continua a ser um sinal manual valido; alem
dela, uma atividade pode ser candidata a partir dos seus intervalos. Cada esforco de 180 s continua a ser UM por
atividade (o maximo movel de 180 s de `power_curves`): os intervalos so acrescentam evidencia a esse esforco, nunca
criam um segundo registo (nao ha duplicacao), e o valor do P0 continua a ser o MMP3 (maximo movel) do registo, nunca a
potencia media do intervalo.

Classes (por atividade, face a referencia MMP3 valida ANTERIOR `ref` e ao maximo movel de 180 s `w180`):
  intervalo_3min_automatico  UM intervalo isolado (n = 1) de 175-185 s com potencia media >= LIMIAR x ref e
                             >= LIMIAR x w180 (o pico de 180 s e esse intervalo, nao outro trecho). Unica classe
                             que equivale a um sinal de teste (origem `intervalo_detectado`).
  a_verificar_repeticoes     intervalo de 175-185 s com potencia ok mas em n >= 2 repeticoes: series repetidas sao
                             treino, nao um unico esforco maximo; fica para confirmacao.
  a_verificar_pico_fora      intervalo isolado de 175-185 s com potencia ok mas w180 supera-o: o pico de 180 s esta
                             noutro trecho da atividade; fica para confirmacao.
  a_verificar_intervalo_longo  intervalo de 186-360 s com potencia media >= LIMIAR x ref (ex. 5 min). A media de
                             300 s NAO e um maximo de 180 s: nunca e YES; fica para confirmacao.
  intervalo_3min_abaixo_limiar  ha intervalo de 175-185 s mas abaixo do limiar de potencia.
  sem_intervalo_3min         ha intervalos com potencia, nenhum nas janelas acima.
  sem_dados_de_intervalos    sem `interval_summary` utilizavel com potencia.
Janelas de duracao (justificadas pelos dados reais: 3486 grupos com potencia; os de 179-182 s concentram 151
agrupamentos estruturados, o resto espalha-se 2-4 por segundo): 175-185 s = +-5 s do bloco de 3 min; 186-360 s =
candidatos mais longos so para inspecao (diagnostico), nunca elegiveis. Sao constantes explicitas e testadas.
O limiar de potencia e o existente (0,92); nao esta validado fisiologicamente para intervalos.
"""
import json
import math
import re

JANELA_3MIN = (175, 185)
JANELA_LONGA = (186, 360)

C_AUTO = 'intervalo_3min_automatico'
C_REPETICOES = 'a_verificar_repeticoes'
C_PICO_FORA = 'a_verificar_pico_fora'
C_LONGO = 'a_verificar_intervalo_longo'
C_ABAIXO = 'intervalo_3min_abaixo_limiar'
C_SEM_3MIN = 'sem_intervalo_3min'
C_SEM_DADOS = 'sem_dados_de_intervalos'
A_VERIFICAR = (C_REPETICOES, C_PICO_FORA, C_LONGO)

ORIGEM_TAG = 'tag_manual'
ORIGEM_INTERVALO = 'intervalo_detectado'

_RE_ITEM = re.compile(r'^\s*(\d+)\s*x\s+((?:\d+\s*h)?\s*(?:\d+\s*m)?\s*(?:\d+\s*s)?)\s+(\d+(?:[.,]\d+)?)\s*(w|bpm)\s*$',
                      re.I)
_RE_PARTE = re.compile(r'(\d+)\s*([hms])', re.I)


def _segundos(texto):
    partes = _RE_PARTE.findall(texto or '')
    if not partes:
        return None
    mult = {'h': 3600, 'm': 60, 's': 1}
    return sum(int(v) * mult[u.lower()] for v, u in partes)


def itens_do_resumo(atividade):
    """Lista de textos do `interval_summary` (lista nativa, texto JSON ou vazio -> [])."""
    if not isinstance(atividade, dict):
        return []
    v = atividade.get('interval_summary')
    if isinstance(v, str):
        t = v.strip()
        if t[:1] == '[':
            try:
                v = json.loads(t)
            except ValueError:
                return []
        else:
            return [t] if t else []
    if isinstance(v, (list, tuple)):
        return [x for x in v if isinstance(x, str) and x.strip()]
    return []


def grupos_de_potencia(atividade):
    """({grupos com potencia}, n_itens, n_sem_potencia_ou_ilegivel). Cada grupo: {n, duracao_s, watts}.
    Itens em bpm, em ritmo ou ilegiveis nao tem potencia utilizavel e so se contam."""
    itens = itens_do_resumo(atividade)
    grupos, descartados = [], 0
    for it in itens:
        m = _RE_ITEM.match(it)
        if not m or m.group(4).lower() != 'w':
            descartados += 1
            continue
        dur = _segundos(m.group(2))
        w = float(m.group(3).replace(',', '.'))
        if int(m.group(1)) < 1 or not dur or dur <= 0 or not math.isfinite(w) or w <= 0:
            descartados += 1
            continue
        grupos.append({'n': int(m.group(1)), 'duracao_s': dur, 'watts': w})
    return grupos, len(itens), descartados


def _dentro(d, janela):
    return janela[0] <= d <= janela[1]


def classificar(atividade, w180, ref, limiar):
    """Classe do esforco a partir dos intervalos da atividade, de `w180` (maximo movel de 180 s) e de `ref` (melhor MMP3
    valido ANTERIOR; None se nao existe). Puro e deterministico. Devolve {classe, origem, intervalo, n_grupos, ...}."""
    grupos, n_itens, n_desc = grupos_de_potencia(atividade)
    out = {'classe': C_SEM_DADOS, 'origem': None, 'intervalo': None, 'n_itens': n_itens, 'n_grupos_potencia': len(grupos),
           'n_grupos_3min': 0, 'n_grupos_3min_potencia_ok': 0, 'n_grupos_longos_potencia_ok': 0, 'n_descartados': n_desc}
    if not grupos:
        return out
    out['classe'] = C_SEM_3MIN
    ok = (lambda g: ref is not None and g['watts'] >= limiar * ref)    # noqa: E731
    g3 = [g for g in grupos if _dentro(g['duracao_s'], JANELA_3MIN)]
    out['n_grupos_3min'] = len(g3)
    g3ok = [g for g in g3 if ok(g)]
    out['n_grupos_3min_potencia_ok'] = len(g3ok)
    longos = [g for g in grupos if _dentro(g['duracao_s'], JANELA_LONGA) and ok(g)]
    out['n_grupos_longos_potencia_ok'] = len(longos)
    if g3ok:
        unico = [g for g in g3ok if g['n'] == 1]
        if unico:
            melhor = max(unico, key=lambda g: g['watts'])
            if w180 is not None and melhor['watts'] >= limiar * w180:
                return {**out, 'classe': C_AUTO, 'origem': ORIGEM_INTERVALO, 'intervalo': melhor}
            return {**out, 'classe': C_PICO_FORA, 'intervalo': melhor}
        return {**out, 'classe': C_REPETICOES, 'intervalo': max(g3ok, key=lambda g: g['watts'])}
    if longos:
        return {**out, 'classe': C_LONGO, 'intervalo': max(longos, key=lambda g: g['watts'])}
    if g3:
        return {**out, 'classe': C_ABAIXO, 'intervalo': max(g3, key=lambda g: g['watts'])}
    return out
