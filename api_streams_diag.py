"""api_streams_diag.py — que streams existem mesmo nas actividades.

Serve para responder a uma pergunta que so' os dados podem responder: como e'
que a Intervals.icu chama, nesta conta, os streams de RR, DFA-a1, frequencia
respiratoria e RRa1 que o alphaHRV grava no FIT. Adivinhar nomes de campos ja'
custou duas rondas neste projecto -- o EBP e a Fractional Utilization nunca
existiram com os nomes que eu tinha assumido.

Registado com:  import api_streams_diag; api_streams_diag.registar(app)
"""

import traceback
from datetime import datetime, timedelta

from flask import jsonify, request

# Nomes que valem a pena procurar. A lista serve so' para CLASSIFICAR o que
# aparecer; o endpoint devolve sempre todos os streams encontrados, incluindo
# os que nao estao aqui.
INTERESSE = {
    'rr': ['rr', 'rr_intervals', 'rrintervals', 'hrv', 'hrv_rmssd', 'ibi'],
    'dfa_a1': ['dfa_a1', 'dfaa1', 'alpha1', 'a1', 'dfa'],
    'respiracao': ['respiration', 'respiration_rate', 'resp', 'breathing'],
    'rra1': ['rra1', 'rr_a1', 'meanrra1'],
    'prontidao': ['readiness'],
    'smo2': ['smo2', 'thb'],
}


def _classificar(nome):
    n = ''.join(c for c in str(nome).lower() if c.isalnum())
    for grupo, alvos in INTERESSE.items():
        for a in alvos:
            if ''.join(c for c in a if c.isalnum()) == n:
                return grupo
    for grupo, alvos in INTERESSE.items():
        for a in alvos:
            aa = ''.join(c for c in a if c.isalnum())
            if aa and (aa in n or n in aa):
                return grupo + '?'
    return None


def registar(app):

    @app.route('/api/diag/streams')
    def api_diag_streams():
        """Que streams existem nas actividades recentes.

        ?dias=60   janela        ?tipo=Ride   filtrar por tipo
        ?n=8       quantas actividades inspeccionar
        ?id=123    inspeccionar uma actividade concreta
        """
        try:
            import api_client as api

            uma = request.args.get('id')
            passos = []          # porque e' que sobraram N actividades
            if uma:
                ids = [uma]
                meta = {uma: {}}
            else:
                dias = request.args.get('dias', type=int) or 60
                n = min(request.args.get('n', type=int) or 8, 25)
                tipo = request.args.get('tipo')
                oldest = (datetime.now() - timedelta(days=dias)).strftime('%Y-%m-%d')
                # icu_get devolve (data, erro) -- nao a lista directamente.
                # Tratar o tuplo como lista fazia com que o achatamento
                # devolvesse zero e o endpoint dissesse "0 actividades" sem
                # explicar nada.
                bruto, erro = api.icu_get(
                    f'/athlete/{api.ATHLETE_ID}/activities',
                    {'oldest': oldest,
                     'newest': datetime.now().strftime('%Y-%m-%d')})
                passos.append({'passo': 'resposta da API',
                               'forma': type(bruto).__name__,
                               'erro': erro,
                               'n': len(bruto) if hasattr(bruto, '__len__') else None})
                if erro:
                    return jsonify({'status': 'erro',
                                    'mensagem': f'API: {erro}',
                                    'como_se_chegou_aqui': passos}), 200
                acts = bruto or []
                if isinstance(acts, dict):
                    acts = acts.get('content') or acts.get('activities') or []
                # a API devolve por vezes uma lista de listas; achatar e
                # ficar so' com dicionarios, senao o .get rebenta
                planas = []
                for x in acts:
                    if isinstance(x, dict):
                        planas.append(x)
                    elif isinstance(x, list):
                        planas.extend(y for y in x if isinstance(y, dict))
                acts = planas
                passos.append({'passo': 'apos achatar', 'n': len(acts)})

                tipos_vistos = {}
                for a in acts:
                    t = a.get('type')
                    tipos_vistos[t] = tipos_vistos.get(t, 0) + 1
                passos.append({'passo': 'tipos encontrados', 'tipos': tipos_vistos})

                if tipo:
                    # aceitar tanto o tipo da API ('VirtualRide') como a
                    # modalidade do dashboard ('Bike'), senao filtrar por
                    # 'Ride' devolve zero quando tudo e' VirtualRide
                    try:
                        from config import TYPE_MAP
                        variantes = {k for k, v in TYPE_MAP.items() if v == tipo}
                    except Exception:
                        variantes = set()
                    variantes.add(tipo)
                    acts = [a for a in acts if a.get('type') in variantes]
                    passos.append({'passo': f'apos filtrar por {tipo}',
                                   'variantes_aceites': sorted(variantes),
                                   'n': len(acts)})

                acts = acts[:n]
                ids = [a.get('id') for a in acts if a.get('id')]
                passos.append({'passo': 'ids com que se vai buscar streams',
                               'n': len(ids)})
                meta = {a.get('id'): {
                    'data': (a.get('start_date_local') or '')[:10],
                    'tipo': a.get('type'), 'nome': (a.get('name') or '')[:60],
                } for a in acts}

            por_actividade, todos = {}, {}
            for aid in ids:
                try:
                    s, err = api.icu_get(f'/activity/{aid}/streams')
                except Exception as e:
                    por_actividade[aid] = {'erro': f'{type(e).__name__}: {e}'}
                    continue
                if err:
                    por_actividade[aid] = {**(meta.get(aid) or {}), 'erro': err}
                    continue
                if isinstance(s, dict):
                    s = s.get('streams') or s.get('content') or []
                nomes = []
                for st in (s or []):
                    if not isinstance(st, dict):
                        continue
                    nome = st.get('type') or st.get('name')
                    if not nome:
                        continue
                    dados = st.get('data') or []
                    nomes.append({
                        'stream': nome,
                        'nome_sensor': st.get('name'),
                        'n_pontos': len(dados) if isinstance(dados, list) else None,
                        'classificacao': _classificar(nome),
                        'amostra': [v for v in dados[:5]] if isinstance(dados, list) else None,
                    })
                    todos[nome] = todos.get(nome, 0) + 1
                por_actividade[aid] = {**(meta.get(aid) or {}),
                                       'n_streams': len(nomes),
                                       'streams': nomes}

            encontrados = {}
            for nome in todos:
                c = _classificar(nome)
                if c:
                    encontrados.setdefault(c, []).append(nome)

            return jsonify({
                'status': 'ok',
                'como_se_chegou_aqui': passos,
                'actividades_inspeccionadas': len(ids),
                'streams_por_actividade': por_actividade,
                'todos_os_streams': dict(sorted(todos.items(),
                                                key=lambda kv: -kv[1])),
                'de_interesse_encontrados': encontrados,
                'de_interesse_em_falta': [g for g in INTERESSE
                                          if g not in encontrados],
                'nota': ('todos_os_streams lista o que existe mesmo, com a '
                         'contagem de actividades em que aparece. Se o RR ou '
                         'o DFA-a1 nao estiverem la, nao ha nada a calcular '
                         'a partir deles -- e melhor saber isso antes de '
                         'escrever o codigo do que depois')})
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    @app.route('/api/hrv/sessoes')
    def api_hrv_sessoes():
        """Sessoes com dados de alphaHRV, com o id para usar nos limiares.

        Existe para nao ser preciso adivinhar ids: lista as sessoes que tem
        MeanRRa1 (ou seja, onde o alphaHRV correu), com a ligacao pronta.

        ?modalidade=Bike   ?dias=365   ?n=30
        """
        try:
            import db as _db
            import json as _json
            dias = request.args.get('dias', type=int) or 365
            n = min(request.args.get('n', type=int) or 30, 200)
            modalidade = request.args.get('modalidade')
            corte = (datetime.now() - timedelta(days=dias)).strftime('%Y-%m-%d')

            cond, args = ["raw IS NOT NULL", "date >= ?"], [corte]
            if modalidade:
                from config import TYPE_MAP
                variantes = [k for k, v in TYPE_MAP.items() if v == modalidade]
                if variantes:
                    cond.append(f"type IN ({','.join('?' * len(variantes))})")
                    args += variantes
            linhas = _db._exec(
                f"""SELECT id, type, date, name, raw FROM activities
                     WHERE {' AND '.join(cond)}
                     ORDER BY date DESC""", tuple(args), fetch='all') or []

            fora = []
            for aid, tipo, data, nome, raw in linhas:
                try:
                    j = raw if isinstance(raw, dict) else _json.loads(raw)
                except Exception:
                    continue
                mean = (j or {}).get('MeanRRa1') or (j or {}).get('MeanRRA1')
                if mean is None:
                    continue
                fora.append({
                    'id': aid, 'tipo': tipo, 'data': str(data)[:10],
                    'nome': (nome or '')[:60],
                    'MeanRRa1': round(float(mean), 4),
                    'HRVT1': (j or {}).get('HRVT1'),
                    'HRVT2': (j or {}).get('HRVT2'),
                    'duracao_min': (round((j or {}).get('moving_time', 0) / 60)
                                    if (j or {}).get('moving_time') else None),
                    'limiares': f'/api/hrv/limiares/{aid}',
                })
                if len(fora) >= n:
                    break
            return jsonify({
                'status': 'ok', 'n': len(fora), 'dias': dias,
                'modalidade': modalidade, 'sessoes': fora,
                'nota': ('copia um id da coluna "limiares" para o browser. '
                         'Para o HRVT1c a sessao ideal e uma rampa ou '
                         'progressiva: precisa de um inicio facil, onde o a1 '
                         'esta alto, e de subir depois')})
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    @app.route('/api/hrv/limiares/<path:activity_id>')
    def api_hrv_limiares(activity_id):
        """HRVT1s, HRVT2 e HRVT1c calculados dos streams desta actividade.

        ?early=300   segundos iniciais para o maximo de a1
        """
        try:
            import os as _os
            import sys as _sys
            _sys.path.insert(0, _os.path.join(
                _os.path.dirname(_os.path.abspath(__file__)), 'utils'))
            import hrv_limiares as hl
            import api_client as api

            # limpar o id: e' facil colar o caminho inteiro por engano, ou
            # deixar o <activity_id> do exemplo
            aid = str(activity_id).strip().strip('/').split('/')[-1]
            if not aid or aid.startswith('<') or not aid[0].isalnum():
                return jsonify({
                    'status': 'erro',
                    'mensagem': (f'id invalido: "{activity_id}". Usa so o id '
                                 'da actividade, por exemplo '
                                 '/api/hrv/limiares/i118432383'),
                    'onde_encontrar_ids': '/api/hrv/sessoes?modalidade=Bike'}), 200

            bruto, err = api.icu_get(f'/activity/{aid}/streams')
            if err:
                return jsonify({
                    'status': 'erro', 'mensagem': f'API: {err}',
                    'id_usado': aid,
                    'onde_encontrar_ids': '/api/hrv/sessoes?modalidade=Bike'}), 200
            lista = bruto
            if isinstance(lista, dict):
                lista = lista.get('streams') or lista.get('content') or []

            streams, nomes = {}, []
            for st in (lista or []):
                if not isinstance(st, dict):
                    continue
                nome = st.get('type') or st.get('name')
                if not nome:
                    continue
                nomes.append(nome)
                streams[nome] = st.get('data') or []

            # aceitar as varias grafias sem assumir nenhuma
            def _achar(*alvos):
                for a in alvos:
                    for n in streams:
                        if ''.join(c for c in n.lower() if c.isalnum()) == \
                                ''.join(c for c in a.lower() if c.isalnum()):
                            return n
                return None

            mapa = {
                'dfa_a1': _achar('dfa_a1', 'dfaa1', 'alpha1', 'a1'),
                'respiration': _achar('respiration', 'RespirationRateAlphaHRV',
                                      'respiration_rate'),
                'watts': _achar('watts', 'power'),
                'heartrate': _achar('heartrate', 'hr'),
                'artifacts': _achar('artifacts', 'artifact'),
            }
            usados = {k: streams.get(v) for k, v in mapa.items() if v}

            res = hl.calcular(
                usados,
                early_s=request.args.get('early', type=int) or hl.EARLY_RAMP_S,
                artefacto_max=request.args.get('artefacto_max', type=float)
                or 5.0)
            res['status'] = 'ok'
            res['activity_id'] = aid
            res['streams_na_actividade'] = sorted(nomes)
            res['streams_usados'] = {k: v for k, v in mapa.items() if v}
            res['meanrra1_replicado'] = hl.replicar_meanrra1(usados)
            res['rra1_quebra'] = hl.limiares_rra1(usados)
            return jsonify(res)
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    @app.route('/api/hrv/varrer')
    def api_hrv_varrer():
        """Corre os limiares em TODAS as sessoes com alphaHRV.

        Existe porque a pergunta -- o HRVT1 do script sobrestima o limiar? --
        nao se responde numa sessao. Precisa de saber quantas sessoes tem
        dados utilizaveis, e nas que tem, como e' que o valor calculado se
        compara com o do script.

        ?modalidade=Bike  ?dias=365  ?n=40  ?so_utilizaveis=1
        """
        try:
            import os as _os
            import sys as _sys
            import json as _json
            _sys.path.insert(0, _os.path.join(
                _os.path.dirname(_os.path.abspath(__file__)), 'utils'))
            import hrv_limiares as hl
            import api_client as api
            import db as _db

            dias = request.args.get('dias', type=int) or 365
            n_max = min(request.args.get('n', type=int) or 40, 150)
            modalidade = request.args.get('modalidade')
            so_uteis = request.args.get('so_utilizaveis') in ('1', 'true')
            corte = (datetime.now() - timedelta(days=dias)).strftime('%Y-%m-%d')

            cond, args = ["raw IS NOT NULL", "date >= ?"], [corte]
            if modalidade:
                from config import TYPE_MAP
                variantes = [k for k, v in TYPE_MAP.items() if v == modalidade]
                if variantes:
                    cond.append(f"type IN ({','.join('?' * len(variantes))})")
                    args += variantes
            linhas = _db._exec(
                f"""SELECT id, type, date, name, raw FROM activities
                     WHERE {' AND '.join(cond)} ORDER BY date DESC""",
                tuple(args), fetch='all') or []

            alvo = []
            for aid, tipo, data, nome, raw in linhas:
                try:
                    j = raw if isinstance(raw, dict) else _json.loads(raw)
                except Exception:
                    continue
                if ((j or {}).get('MeanRRa1') or (j or {}).get('MeanRRA1')) is None:
                    continue
                alvo.append({'id': aid, 'tipo': tipo, 'data': str(data)[:10],
                             'nome': (nome or '')[:50],
                             'HRVT1_script': (j or {}).get('HRVT1'),
                             'HRVT2_script': (j or {}).get('HRVT2'),
                             'MeanRRa1': (j or {}).get('MeanRRa1')})
                if len(alvo) >= n_max:
                    break

            # buscar os streams em paralelo
            pedidos = {a['id']: (f"/activity/{a['id']}/streams", None)
                       for a in alvo}
            try:
                respostas = api.icu_get_many(pedidos)
            except Exception:
                respostas = {k: api.icu_get(v[0]) for k, v in pedidos.items()}

            def _achar(streams, *alvos):
                for a in alvos:
                    aa = ''.join(c for c in a.lower() if c.isalnum())
                    for nome_s in streams:
                        if ''.join(c for c in nome_s.lower() if c.isalnum()) == aa:
                            return nome_s
                return None

            fora, resumo = [], {
                'sem_stream_dfa': 0, 'utilizaveis': 0, 'nao_utilizaveis': 0,
                'erro': 0}
            for a in alvo:
                r = respostas.get(a['id'])
                dados, err = r if isinstance(r, tuple) else (r, None)
                if err or not dados:
                    resumo['erro'] += 1
                    fora.append({**a, 'estado': 'erro', 'motivo': str(err)[:80]})
                    continue
                lista = dados
                if isinstance(lista, dict):
                    lista = lista.get('streams') or lista.get('content') or []
                streams = {}
                for st in (lista or []):
                    if isinstance(st, dict) and (st.get('type') or st.get('name')):
                        streams[st.get('type') or st.get('name')] = st.get('data') or []
                mapa = {
                    'dfa_a1': _achar(streams, 'dfa_a1', 'dfaa1', 'alpha1'),
                    'respiration': _achar(streams, 'respiration',
                                          'RespirationRateAlphaHRV'),
                    'watts': _achar(streams, 'watts', 'power'),
                    'heartrate': _achar(streams, 'heartrate'),
                    'artifacts': _achar(streams, 'artifacts'),
                }
                if not mapa['dfa_a1']:
                    resumo['sem_stream_dfa'] += 1
                    fora.append({**a, 'estado': 'sem dfa_a1'})
                    continue
                usados = {k: streams.get(v) for k, v in mapa.items() if v}
                res = hl.calcular(usados)
                rra1 = hl.limiares_rra1(usados)
                if not res.get('ok'):
                    resumo['erro'] += 1
                    fora.append({**a, 'estado': 'erro', 'motivo': res.get('motivo')})
                    continue

                lin = {**a,
                       'estado': 'utilizavel' if res['sessao_adequada'] else 'fraca',
                       'duracao_min': round(res['duracao_s'] / 60),
                       'amplitude_w': res['amplitude_potencia_w'],
                       'pct_artefacto': res['pct_descartado_por_artefacto'],
                       'a1_max_inicial': res['a1_max_inicial'],
                       'a1_alvo_hrvt1c': res['a1_alvo_hrvt1c'],
                       'motivo': (None if res['sessao_adequada']
                                  else res['nota_qualidade'])}
                if rra1.get('ok'):
                    lin.update({
                        'VT1_bpm': rra1['VT1_bpm'], 'VT1_dp': rra1['VT1_dp'],
                        'VT2_bpm': rra1['VT2_bpm'], 'VT2_dp': rra1['VT2_dp'],
                        'PT1_w': rra1['PT1_w'], 'PT2_w': rra1['PT2_w'],
                        'fc_coberta': rra1['intervalo_fc']})
                else:
                    lin['rra1_motivo'] = rra1.get('motivo')
                for nome_l, l in (res.get('limiares') or {}).items():
                    for eixo in ('watts', 'heartrate'):
                        d = l.get(eixo) or {}
                        if d.get('ok'):
                            lin[f'{nome_l}_{eixo}'] = d['valor']
                            if eixo == 'watts':
                                lin[f'{nome_l}_degrau'] = d.get('degrau')
                resumo['utilizaveis' if res['sessao_adequada']
                       else 'nao_utilizaveis'] += 1
                fora.append(lin)

            if so_uteis:
                fora = [x for x in fora if x.get('estado') == 'utilizavel']

            # comparar o calculado com o do script, nas que tem os dois
            comp = []
            for x in fora:
                if not x.get('HRVT1_script'):
                    continue
                linha = {'data': x['data'], 'id': x['id'],
                         'script_HRVT1': x['HRVT1_script'],
                         'HRVT1c': x.get('HRVT1c_heartrate'),
                         'VT1_quebra': x.get('VT1_bpm'),
                         'VT1_dp': x.get('VT1_dp')}
                if x.get('HRVT1c_heartrate'):
                    linha['diff_c_menos_script'] = round(
                        x['HRVT1c_heartrate'] - x['HRVT1_script'], 1)
                if x.get('VT1_bpm'):
                    linha['diff_quebra_menos_script'] = round(
                        x['VT1_bpm'] - x['HRVT1_script'], 1)
                comp.append(linha)
            difs = [c['diff_c_menos_script'] for c in comp
                    if c.get('diff_c_menos_script') is not None]
            difq = [c['diff_quebra_menos_script'] for c in comp
                    if c.get('diff_quebra_menos_script') is not None]
            dps = [c['VT1_dp'] for c in comp if c.get('VT1_dp') is not None]

            return jsonify({
                'status': 'ok', 'modalidade': modalidade, 'dias': dias,
                'sessoes_com_alphahrv': len(alvo),
                'resumo': resumo,
                'comparacao_com_script': {
                    'n': len(comp),
                    'HRVT1c_vs_script_mediana': (sorted(difs)[len(difs) // 2]
                                                 if difs else None),
                    'quebra_vs_script_mediana': (sorted(difq)[len(difq) // 2]
                                                 if difq else None),
                    'n_quebra': len(difq),
                    'dp_mediano_da_quebra': (sorted(dps)[len(dps) // 2]
                                             if dps else None),
                    'detalhe': comp},
                'sessoes': fora,
                'nota': ('estado=utilizavel exige amplitude de potencia >= 80 W, '
                         'menos de 30% de pontos descartados por artefacto na FC, '
                         'e pelo menos um limiar atingido. As outras nao servem '
                         'para calcular limiar nenhum -- e isso nao e um defeito '
                         'do metodo, e a sessao nao ter passado pelas '
                         'intensidades certas ou a cinta ter falhado')})
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    @app.route('/api/diag/campos_hrv')
    def api_diag_campos_hrv():
        """Campos do SUMARIO da actividade relacionados com HRV.

        Os HRVT1/HRVT2 vem de custom fields calculados por script no
        Intervals.icu, portanto chegam no sumario e nao nos streams. Isto
        mostra quais existem e com que cobertura.
        """
        try:
            import db as _db
            import json as _json
            dias = request.args.get('dias', type=int) or 365
            corte = (datetime.now() - timedelta(days=dias)).strftime('%Y-%m-%d')
            linhas = _db._exec(
                """SELECT type, date, raw FROM activities
                    WHERE raw IS NOT NULL AND date >= ?
                    ORDER BY date DESC""", (corte,), fetch='all') or []
            padrao = ('hrv', 'dfa', 'a1', 'rr', 'resp', 'alpha', 'readiness')
            campos = {}
            for tipo, data, raw in linhas:
                try:
                    j = raw if isinstance(raw, dict) else _json.loads(raw)
                except Exception:
                    continue
                for k, v in (j or {}).items():
                    if not any(p in k.lower() for p in padrao):
                        continue
                    if v is None:
                        continue
                    e = campos.setdefault(k, {'n': 0, 'tipos': {},
                                              'exemplo': v,
                                              'primeira': None, 'ultima': None})
                    e['n'] += 1
                    e['tipos'][tipo] = e['tipos'].get(tipo, 0) + 1
                    d = str(data)[:10]
                    e['ultima'] = e['ultima'] or d
                    e['primeira'] = d
            return jsonify({
                'status': 'ok', 'actividades': len(linhas), 'dias': dias,
                'campos': dict(sorted(campos.items(),
                                      key=lambda kv: -kv[1]['n']))})
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    return app
