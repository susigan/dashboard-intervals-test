"""Protocolo de testes de validacao.

Porque existe
-------------
A calibracao nos dados de treino falha por tres razoes que a estatistica
nao resolve:

  1. Causalidade invertida — treinas mais quando te sentes bem, por isso
     carga e HRV correlacionam-se no sentido errado.
  2. A CP por sessao nao mede capacidade, mede o que fizeste nesse dia.
  3. Um ponto de HRV por dia da o nivel, nao a trajectoria de recuperacao.

Um teste maximo padronizado resolve os dois primeiros: o estimulo passa a
ser fixado pelo protocolo em vez de escolhido consoante o estado, e o
resultado mede capacidade. Sao os pontos-ancora que faltam.

Como se deteta um teste
-----------------------
Nao e preciso marcar nada a mao. Uma sessao conta como teste valido para
uma duracao se o esforco nessa duracao chegar perto do teu melhor recente:
e o que distingue um esforco maximo de um treino qualquer.

RECUPERAÇÃO HRV (pré-registo 2026-08-13)
-----------------------------------------
Depois de sessões RPE ≥7, HRV atinge o mínimo ao dia +10 (d=-0.378).
Modulador principal: TSB (r²=6.5%).
- TSB negativo (cansaço): efeito maior
- TSB positivo (forma): efeito menor
Revisar a cada 3 meses (próximo: 2026-11-13).
"""

from datetime import datetime, timedelta

# Duracoes que servem de teste, com o que cada uma mede
TESTES = {
    300:  {'nome': '5 min max', 'mede': 'potencia aerobia maxima / VO2max',
           'intervalo_dias': 21},
    1200: {'nome': '20 min max', 'mede': 'limiar funcional / CP',
           'intervalo_dias': 28},
    60:   {'nome': '1 min max', 'mede': 'capacidade anaerobia / W prime',
           'intervalo_dias': 28},
}

# Percentagem do melhor recente a partir da qual o esforco conta como maximo
# Nao e preciso um teste formal. Uma prova, um bloco de intervalos duro ou
# uma saida em grupo servem — o que conta e ter ido perto do maximo naquela
# duracao. 95% e exigente; 92% apanha mais esforcos genuinos sem deixar
# entrar treinos moderados.
LIMIAR_ESFORCO = 0.92
JANELA_MELHOR = 180          # dias para "melhor recente"

# Dimensao da amostra de candidatos a 20 min. Valores ja existentes (antes
# espalhados como literais em robustez() e em pmc.calibrar_com_ancora), apenas
# nomeados: nao foram alterados nem unificados.
ROBUSTEZ_BOM = 30            # robustez(): estado 'bom'
ROBUSTEZ_PARCIAL = 12        # robustez(): estado 'parcial'
MINIMO_CALIBRACAO = 25       # = default de pmc.calibrar_com_ancora(minimo_testes)


def detectar_testes(curvas, limiar=LIMIAR_ESFORCO, janela=JANELA_MELHOR):
    """Sessoes em que houve esforco maximo, por modalidade e duracao.

    curvas: saida de db.load_power_curves() — uma entrada por sessao com
    secs, watts, date e type.
    """
    if not curvas:
        return {}

    por_mod = {}
    for c in curvas:
        por_mod.setdefault(c['type'], []).append(c)

    out = {}
    for mod, lista in por_mod.items():
        lista = sorted(lista, key=lambda x: x['date'])
        resultado = {}
        for secs, info in TESTES.items():
            pontos = []
            for c in lista:
                w = None
                for s, v in zip(c['secs'], c['watts']):
                    if s == secs and isinstance(v, (int, float)) and v > 0:
                        w = float(v)
                        break
                if w is not None:
                    pontos.append({'date': c['date'], 'watts': w,
                                   'activity_id': c['activity_id']})
            if len(pontos) < 5:
                continue

            # melhor dos ultimos `janela` dias antes de cada sessao
            testes = []
            for i, p in enumerate(pontos):
                limite = (datetime.strptime(p['date'], '%Y-%m-%d')
                          - timedelta(days=janela)).strftime('%Y-%m-%d')
                anteriores = [q['watts'] for q in pontos[:i + 1]
                              if q['date'] >= limite]
                if len(anteriores) < 3:
                    continue
                melhor = max(anteriores)
                frac = p['watts'] / melhor if melhor > 0 else 0
                if frac >= limiar:
                    testes.append({**p, 'pct_do_melhor': round(frac * 100, 1),
                                   'melhor_recente': round(melhor, 1)})

            if testes:
                ultimo = testes[-1]
                dias = (datetime.now()
                        - datetime.strptime(ultimo['date'], '%Y-%m-%d')).days
                resultado[secs] = {
                    'nome': info['nome'], 'mede': info['mede'],
                    'n_testes': len(testes),
                    'ultimo': ultimo,
                    'dias_desde_ultimo': dias,
                    'intervalo_recomendado': info['intervalo_dias'],
                    'em_atraso': dias > info['intervalo_dias'],
                    # todos, para calibrar; os ultimos, para mostrar
                    'testes': testes,
                    'recentes': testes[-12:],
                }
        if resultado:
            out[mod] = resultado
    return out


def sugerir(deteccao, modalidades_activas=None):
    """Que testes fazer a seguir, por ordem de urgencia."""
    sug = []
    for mod, durs in (deteccao or {}).items():
        if modalidades_activas and mod not in modalidades_activas:
            continue
        for secs, d in durs.items():
            if not d['em_atraso']:
                continue
            atraso = d['dias_desde_ultimo'] - d['intervalo_recomendado']
            sug.append({
                'modalidade': mod, 'secs': secs, 'nome': d['nome'],
                'mede': d['mede'],
                'dias_desde_ultimo': d['dias_desde_ultimo'],
                'atraso_dias': atraso,
                'ultimo_valor': d['ultimo']['watts'],
                'ultima_data': d['ultimo']['date'],
                'urgencia': ('alta' if atraso > d['intervalo_recomendado']
                             else 'media'),
            })
    sug.sort(key=lambda x: -x['atraso_dias'])
    return sug


def serie_ancora(deteccao, mod=None, secs=1200):
    """Serie de performance so com dias de teste.

    E esta que deve alimentar a calibracao em vez da CP de todas as sessoes:
    poucos pontos, mas cada um mede capacidade em vez de escolha de treino.
    """
    out = []
    for m, durs in (deteccao or {}).items():
        if mod and m != mod:
            continue
        d = durs.get(secs)
        if not d:
            continue
        for t in d['testes']:
            out.append({'date': t['date'], 'valor': t['watts'],
                        'modalidade': m, 'activity_id': t['activity_id']})
    out.sort(key=lambda x: x['date'])
    return out


def robustez(deteccao, secs=1200):
    """Como o sistema se degrada quando faltam testes.

    Um teste falhado nao parte nada: perde-se um ponto-ancora. O que importa
    e quantos pontos restam e ha quanto tempo foi o ultimo. A resposta diz
    exactamente isso, por modalidade.
    """
    out = {}
    for mod, durs in (deteccao or {}).items():
        d = durs.get(secs)
        if not d:
            continue
        testes = d.get('testes') or []
        if len(testes) < 2:
            out[mod] = {'n': len(testes), 'estado': 'insuficiente'}
            continue

        # intervalo tipico entre testes, nos dados reais
        dias = []
        for a, b in zip(testes[:-1], testes[1:]):
            da = datetime.strptime(a['date'], '%Y-%m-%d')
            db_ = datetime.strptime(b['date'], '%Y-%m-%d')
            dias.append((db_ - da).days)
        dias_ord = sorted(dias)
        mediana = dias_ord[len(dias_ord) // 2]

        n = len(testes)
        if n >= ROBUSTEZ_BOM:
            estado, nota = 'bom', 'da para calibrar contra performance real'
        elif n >= ROBUSTEZ_PARCIAL:
            estado, nota = ('parcial',
                            f'{n} pontos — da para tendencia, ainda pouco para '
                            'calibrar (~30)')
        else:
            estado, nota = 'insuficiente', f'so {n} pontos'

        out[mod] = {
            'n': n, 'estado': estado, 'nota': nota,
            'intervalo_mediano_dias': mediana,
            'primeiro': testes[0]['date'], 'ultimo': testes[-1]['date'],
            'dias_desde_ultimo': d['dias_desde_ultimo'],
            'perder_um_teste': (
                f'sem consequencia imediata: com {n} pontos e mediana de '
                f'{mediana}d entre eles, falhar um adia a actualizacao mas '
                'nao invalida nada'),
        }
    return out


def cobertura(deteccao):
    """Ha testes suficientes para calibrar? Quantos e com que frequencia."""
    total = 0
    detalhe = {}
    for mod, durs in (deteccao or {}).items():
        for secs, d in durs.items():
            total += d['n_testes']
            detalhe.setdefault(mod, {})[d['nome']] = {
                'n': d['n_testes'],
                'ultimo': d['ultimo']['date'],
                'dias': d['dias_desde_ultimo'],
                'em_atraso': d['em_atraso'],
            }
    # Descritivo: o total junta modalidades com amostras muito diferentes e
    # esforcos so "detetados" (nao confirmados como testes maximos), por isso
    # nao diz se ha amostra suficiente. Isso avalia-se por modalidade em
    # suficiencia_por_modalidade().
    if total:
        veredicto = (f'{total} esforcos detetados em {len(detalhe)} '
                     f'modalidade(s), somando todas as duracoes. Detetado nao '
                     'e o mesmo que teste maximo confirmado, e a suficiencia '
                     'para calibrar avalia-se por modalidade (ver suficiencia).')
    else:
        veredicto = ('Nenhum esforco detetado. Sem esforcos perto do maximo '
                     'nao ha referencia de performance para esta analise.')
    return {'total': total, 'por_modalidade': detalhe, 'veredicto': veredicto}


def _estado_amostra(n):
    """Mesmos limiares de robustez(): 30 'bom', 12 'parcial', senao 'insuficiente'."""
    if n >= ROBUSTEZ_BOM:
        return 'bom'
    if n >= ROBUSTEZ_PARCIAL:
        return 'parcial'
    return 'insuficiente'


def suficiencia_por_modalidade(deteccao, curvas=None, secs=1200,
                               minimo_calibracao=MINIMO_CALIBRACAO):
    """Dimensao da amostra de candidatos, avaliada em cada modalidade.

    Nao usa o total global e nao cria limiares: reutiliza 30/12 (robustez) e
    25 (calibrar_com_ancora). So mede QUANTIDADE de candidatos a `secs`; nao
    diz que sejam testes maximos validos. Um historico de potencia curto
    (p.ex. gravacao de potencia adicionada ha pouco) mostra-se tal como e, sem
    ser tratado como defeito: a amostra e insuficiente, e o texto diz ha
    quanto tempo existem curvas de potencia.

    curvas: saida de db.load_power_curves() (para o periodo do historico).
    """
    historico = {}
    for c in curvas or []:
        mod, d = c.get('type'), str(c.get('date') or '')[:10]
        if not mod or len(d) != 10:
            continue
        historico.setdefault(mod, []).append(d)

    nome = TESTES.get(secs, {}).get('nome', f'{secs} s')
    out = {}
    for mod in sorted(set(deteccao or {}) | set(historico)):
        durs = (deteccao or {}).get(mod) or {}
        d = durs.get(secs)
        testes = (d or {}).get('testes') or []
        n = len(testes)
        n_dias = len({t['date'] for t in testes})

        hist = None
        if historico.get(mod):
            datas = sorted(historico[mod])
            dias = (datetime.strptime(datas[-1], '%Y-%m-%d')
                    - datetime.strptime(datas[0], '%Y-%m-%d')).days
            hist = {'primeiro': datas[0], 'ultimo': datas[-1],
                    'meses': round(dias / 30.4375, 1), 'n_curvas': len(datas)}

        estado = _estado_amostra(n)
        atinge = n >= minimo_calibracao
        texto = (f'{mod}: {n} candidatos de {nome}'
                 + (f' ({n_dias} dias distintos)' if n else ''))
        if hist:
            texto += (f', com {hist["meses"]} meses de historico de potencia '
                      f'(desde {hist["primeiro"]})')
        texto += (f'. Amostra: {estado} (limiares {ROBUSTEZ_PARCIAL}/{ROBUSTEZ_BOM}); '
                  f'minimo para calibrar: {minimo_calibracao} candidatos '
                  + ('atingido' if atinge else 'nao atingido')
                  + '. Mede so a quantidade, nao a validade dos candidatos.')
        out[mod] = {
            'secs': secs, 'nome': nome,
            'n_candidatos': n, 'n_dias_distintos': n_dias,
            'estado': estado,
            'minimo_calibracao': minimo_calibracao,
            'atinge_minimo_calibracao': atinge,
            'ultimo': (d or {}).get('ultimo', {}).get('date') if d else None,
            'outras_duracoes': {
                durs[s]['nome']: durs[s]['n_testes'] for s in sorted(durs) if s != secs},
            'historico_potencia': hist,
            'texto': texto,
        }
    return out


def recomendacao_teste_por_tsb(tsb, modalidade_preferida=None):
    """Recomendação de quando fazer testes máximos, baseada em TSB.
    
    TSB é modulador principal da recuperação HRV (pré-registo 2026-08-13).
    Efeito RPE→HRV varia conforme estado:
    - TSB < -10: cansaço → recuperação mais lenta → evitar testes
    - TSB -10 a 0: transição → ok para testes moderados
    - TSB > 0: forma → ideal para testes máximos
    """
    if tsb is None:
        return {'recomendacao': 'indeterminado', 'motivo': 'TSB não disponível'}
    
    if tsb < -10:
        return {
            'recomendacao': 'aguardar',
            'motivo': f'TSB={tsb:.1f} (muito cansado)',
            'sugestao': 'Recupera mais lentamente após carga; evita testes por agora',
            'revisar_em_dias': 7,
        }
    elif tsb < 0:
        return {
            'recomendacao': 'moderado',
            'motivo': f'TSB={tsb:.1f} (em transição)',
            'sugestao': 'Pode fazer testes curtos (5-20min), evita testes máximos longos',
            'revisar_em_dias': 3,
        }
    else:
        return {
            'recomendacao': 'ideal',
            'motivo': f'TSB={tsb:.1f} (em forma)',
            'sugestao': 'Estado ideal para testes máximos (1min, 5min, 20min)',
            'revisar_em_dias': 1,
        }
