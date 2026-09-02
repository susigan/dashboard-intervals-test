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

    @app.route('/api/hrv/qualidade')
    def api_hrv_qualidade():
        """Distribuicao de artefacto e limiar sugerido, por modalidade.

        Corre o varrimento em todas as modalidades e devolve o limiar
        calibrado na distribuicao de cada uma. E' o que decide que sessoes
        entram nas medianas dos campos externos.

        ?dias=365  ?n=60
        """
        try:
            import os as _os
            import sys as _sys
            _sys.path.insert(0, _os.path.join(
                _os.path.dirname(_os.path.abspath(__file__)), 'utils'))
            import hrv_qualidade as hq
            import json as _json
            import db as _db
            from config import TYPE_MAP

            dias = request.args.get('dias', type=int) or 365
            corte = (datetime.now() - timedelta(days=dias)).strftime('%Y-%m-%d')
            linhas = _db._exec(
                """SELECT id, type, date, raw FROM activities
                    WHERE raw IS NOT NULL AND date >= ?""",
                (corte,), fetch='all') or []

            # o pct de artefacto vem do stream; aqui usa-se o que ja' foi
            # calculado pelo varrer e guardado em memoria nao existe, por
            # isso recolhe-se por modalidade a partir dos campos de HRV
            # presentes -- serve para saber quantas sessoes ha por
            # modalidade antes de gastar chamadas a API
            por_mod = {}
            for aid, tipo, data, raw in linhas:
                mod = TYPE_MAP.get(tipo)
                if not mod:
                    continue
                try:
                    j = raw if isinstance(raw, dict) else _json.loads(raw)
                except Exception:
                    continue
                tem = ((j or {}).get('MeanRRa1') or (j or {}).get('MeanRRA1')) is not None
                e = por_mod.setdefault(mod, {'n': 0, 'com_alphahrv': 0})
                e['n'] += 1
                if tem:
                    e['com_alphahrv'] += 1

            return jsonify({
                'status': 'ok', 'dias': dias,
                'sessoes_por_modalidade': por_mod,
                'como_obter_limiares': (
                    'correr /api/hrv/varrer?modalidade=X para cada modalidade; '
                    'o limiar calibrado vem no campo filtro_artefacto da '
                    'resposta'),
                'regra': (f'limiar = percentil que conserva '
                          f'{int(hq.FRACCAO_MINIMA_CONSERVADA * 100)}% das '
                          f'sessoes da modalidade, com tecto absoluto em '
                          f'{hq.ARTEFACTO_TECTO}%')})
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    @app.route('/api/hrv/aquecimento_inflexao')
    def api_hrv_aquecimento_inflexao():
        """Primeiro ponto de inflexao do DFA-a1 nas escadas de aquecimento.

        Usa os blocos ja' guardados pela tab Aquecimento: cada bloco e' um
        patamar de watts com dfa1 medio. Sao poucos pontos, ordenados e
        iguais entre sessoes -- o oposto das sessoes livres, onde a quebra
        assentava no aquecimento e dava VT1 a 80-105 bpm.

        ?modalidade=Bike  ?dias=730
        """
        try:
            import os as _os
            import sys as _sys
            _sys.path.insert(0, _os.path.join(
                _os.path.dirname(_os.path.abspath(__file__)), 'utils'))
            import hrv_qualidade as hq
            import aquecimento_db as aq_db

            modalidade = request.args.get('modalidade')
            dias = request.args.get('dias', type=int) or 730
            corte = (datetime.now() - timedelta(days=dias)).strftime('%Y-%m-%d')
            conn = aq_db.get_conn()

            cond, args = ["data >= ?"], [corte]
            if modalidade:
                cond.append("modalidade = ?")
                args.append(modalidade)
            rs = conn.execute(
                f"""SELECT activity_id, modalidade, data, bloco_num,
                           watts_alvo, watts_real, hr_avg, dfa1_avg,
                           smo2_avg, resp_avg
                      FROM aquecimento_blocos
                     WHERE {' AND '.join(cond)}
                     ORDER BY modalidade, data, bloco_num""",
                tuple(args)).fetchall()

            sessoes = {}
            for r in rs:
                k = (r['modalidade'], r['activity_id'], r['data'])
                sessoes.setdefault(k, []).append(dict(r))

            por_mod = {}
            for (mod, aid, data), blocos in sorted(sessoes.items()):
                inf_w = hq.inflexao_na_escada(blocos, 'watts_alvo', 'dfa1_avg')
                inf_hr = hq.inflexao_na_escada(blocos, 'hr_avg', 'dfa1_avg')
                e = por_mod.setdefault(mod, {'sessoes': []})
                e['sessoes'].append({
                    'activity_id': aid, 'data': data,
                    'n_blocos': len(blocos),
                    'watts_dos_blocos': [b['watts_alvo'] for b in blocos],
                    'dfa1_dos_blocos': [round(b['dfa1_avg'], 3)
                                        if b['dfa1_avg'] else None
                                        for b in blocos],
                    'inflexao_w': inf_w.get('inflexao'),
                    'a1_na_inflexao': inf_w.get('a1_na_inflexao'),
                    'razao_declives_w': inf_w.get('razao_declives'),
                    'motivo_w': inf_w.get('motivo'),
                    'inflexao_bpm': inf_hr.get('inflexao'),
                    'razao_declives_bpm': inf_hr.get('razao_declives'),
                })

            for mod, e in por_mod.items():
                # so' contam as quebras nitidas: razao de declives >= 2
                nitidas = [s for s in e['sessoes']
                           if s.get('razao_declives_w')
                           and s['razao_declives_w'] >= 2]
                e['resumo_watts'] = hq.resumir_inflexoes(e['sessoes'], 'inflexao_w')
                e['resumo_watts_nitidas'] = hq.resumir_inflexoes(nitidas, 'inflexao_w')
                e['resumo_bpm'] = hq.resumir_inflexoes(e['sessoes'], 'inflexao_bpm')
                e['a1_na_inflexao'] = hq.resumir_inflexoes(
                    [{'inflexao': s['a1_na_inflexao']} for s in e['sessoes']
                     if s.get('a1_na_inflexao')], 'inflexao')
                e['n_sessoes'] = len(e['sessoes'])
                e['n_com_quebra_nitida'] = len(nitidas)

            return jsonify({
                'status': 'ok', 'modalidade': modalidade, 'dias': dias,
                'por_modalidade': por_mod,
                'nota': ('a1_na_inflexao e o valor de DFA-a1 no ponto de '
                         'quebra, medido neste atleta. Se for repetivel '
                         'entre sessoes, e a alternativa individualizada ao '
                         '0.75 da literatura -- e sai de escadas iguais, '
                         'nao de sessoes livres'),
                'nota_repetivel': ('repetivel = intervalo interquartil abaixo '
                                   'de 15% da mediana. E o teste que decide '
                                   'se isto serve de ancora ou nao')})
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

        ?modalidade=Bike  ?dias=365  ?n=500  ?so_utilizaveis=1
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
            # sem limite por omissao: o 40 anterior cortava as 85 sessoes
            # de Bike a meio sem o dizer
            n_max = min(request.args.get('n', type=int) or 500, 500)
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

            # limiar de artefacto calibrado NA DISTRIBUICAO DESTA modalidade
            import hrv_qualidade as hq
            filtro = hq.limiar_por_modalidade(
                [x.get('pct_artefacto') for x in fora
                 if x.get('pct_artefacto') is not None])
            lim = request.args.get('artefacto_max', type=float) or filtro['limiar']
            for x in fora:
                p = x.get('pct_artefacto')
                x['passa_filtro'] = (p is not None and p <= lim)

            if so_uteis:
                fora = [x for x in fora if x.get('estado') == 'utilizavel']

            # comparar o calculado com o do script, nas que tem os dois
            comp = []
            for x in fora:
                if not x.get('HRVT1_script'):
                    continue
                if not x.get('passa_filtro'):
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
                'filtro_artefacto': {**filtro, 'limiar_usado': lim},
                'HRVT1_script_filtrado': hq.resumir_inflexoes(
                    [{'inflexao': x['HRVT1_script']} for x in fora
                     if x.get('HRVT1_script') and x.get('passa_filtro')],
                    'inflexao'),
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

    @app.route('/api/diag/proveniencia')
    def api_diag_proveniencia():
        """De que TIPO de sessão vem cada campo da Intervals.icu.

        O AeT, o Pvo2max, o HRVT1PLUS e o PBP não significam o mesmo em
        todas as sessões: um Pvo2max de um rolo fácil não é um Pvo2max.
        Isto cruza cada campo com o tipo de sessão em que foi medido.

        PRIMEIRO PASSO, barato: usa só o JSON já guardado, sem chamar a API
        400 vezes. Se os laps lá estiverem, classifica-se tudo de graça; se
        não, fica-se a saber quanto custaria antes de o fazer.

        ?modalidade=Bike   ?dias=1095   ?campo=AeT
        """
        try:
            import json as _json
            import db as _db
            from config import TYPE_MAP
            import os as _os
            import sys as _sys
            _sys.path.insert(0, _os.path.join(
                _os.path.dirname(_os.path.abspath(__file__)), 'utils'))
            import mnirs as _mn

            dias = request.args.get('dias', type=int) or 1095
            modalidade = request.args.get('modalidade')
            corte = (datetime.now() - timedelta(days=dias)).strftime('%Y-%m-%d')

            cond, args = ["raw IS NOT NULL", "date >= ?"], [corte]
            if modalidade:
                vs = [k for k, v in TYPE_MAP.items() if v == modalidade]
                if vs:
                    cond.append(f"type IN ({','.join('?' * len(vs))})")
                    args += vs
            linhas = _db._exec(
                f"""SELECT id, type, date, name, raw FROM activities
                     WHERE {' AND '.join(cond)} ORDER BY date DESC""",
                tuple(args), fetch='all') or []

            chaves_intervalos = {}
            amostra_summary = []
            bruto_summary = []
            campos_todos = {}
            lap_counts = []
            com_laps = sem_laps = 0
            por_campo = {}
            classificadas = {}
            exemplos_sem_laps = []

            for aid, tipo, data, nome, raw in linhas:
                try:
                    j = raw if isinstance(raw, dict) else _json.loads(raw)
                except Exception:
                    continue
                laps = None
                # O 'interval_summary' aparece nas 244 actividades e pode
                # trazer o tipo e a duracao de cada intervalo. Se trouxer,
                # classifica-se tudo sem uma unica chamada a API; se so'
                # tiver contagens, ficamos a saber e decidimos.
                isum = (j or {}).get('interval_summary')
                # a amostra veio vazia, portanto isto nao e' uma lista de
                # dicionarios. Regista-se o que E', em bruto, em vez de se
                # continuar a adivinhar o formato.
                if isum is not None and len(bruto_summary) < 3:
                    bruto_summary.append({
                        'id': aid, 'data': str(data)[:10],
                        'tipo_python': type(isum).__name__,
                        'tamanho': (len(isum)
                                    if hasattr(isum, '__len__') else None),
                        'valor': (isum if not hasattr(isum, '__len__')
                                  or len(str(isum)) < 1500
                                  else str(isum)[:1500] + ' …TRUNCADO'),
                    })
                # lista de STRINGS: e' o formato real. Classifica-se aqui,
                # sem chamada a API.
                if (isinstance(isum, list) and isum
                        and isinstance(isum[0], str)):
                    chaves_intervalos['interval_summary(str)'] = \
                        chaves_intervalos.get('interval_summary(str)', 0) + 1
                    try:
                        c = _mn.classificar_de_summary(isum)
                        if c.get('ok'):
                            classificadas[str(aid)] = c['tipo']
                            com_laps += 1
                            if len(amostra_summary) < 5:
                                amostra_summary.append({
                                    'id': aid, 'data': str(data)[:10],
                                    'summary': isum,
                                    'tipo': c['tipo'],
                                    'aquecimento': c.get('aquecimento'),
                                    'treino': c.get('blocos_do_treino')})
                            continue
                    except Exception as e:
                        if len(exemplos_sem_laps) < 3:
                            exemplos_sem_laps.append(
                                {'id': aid, 'erro_classificar': str(e)[:120]})

                if isinstance(isum, list) and isum:
                    amostra = isum[0] if isinstance(isum[0], dict) else None
                    if amostra:
                        chaves_intervalos['interval_summary'] = \
                            chaves_intervalos.get('interval_summary', 0) + 1
                        if not amostra_summary:
                            amostra_summary.append(
                                {'id': aid, 'n_itens': len(isum),
                                 'chaves_do_primeiro': sorted(amostra),
                                 'primeiro': {k2: amostra[k2]
                                              for k2 in list(amostra)[:12]}})
                        # tem tipo e tempos? entao serve como laps
                        if ('type' in amostra
                                and ('start_time' in amostra
                                     or 'moving_time' in amostra
                                     or 'elapsed_time' in amostra)):
                            laps = isum
                for k in ('icu_intervals', 'intervals', 'laps', 'splits'):
                    v = (j or {}).get(k)
                    if v:
                        chaves_intervalos[k] = chaves_intervalos.get(k, 0) + 1
                        if laps is None and isinstance(v, list):
                            laps = v
                if laps:
                    com_laps += 1
                    try:
                        bl = _mn.blocos_de_laps(laps)
                        if bl.get('ok'):
                            c = _mn.classificar_sessao(bl['blocos'], laps=laps)
                            classificadas[str(aid)] = c.get('tipo')
                    except Exception:
                        pass
                elif str(aid) not in classificadas:
                    sem_laps += 1
                    if len(exemplos_sem_laps) < 3:
                        exemplos_sem_laps.append({
                            'id': aid, 'data': str(data)[:10],
                            'n_chaves_no_json': len(j or {}),
                            'chaves_parecidas': sorted(
                                k for k in (j or {})
                                if any(x in k.lower()
                                       for x in ('interv', 'lap', 'split')))})

                for k in (j or {}):
                    campos_todos[k] = campos_todos.get(k, 0) + 1

                lc = (j or {}).get('icu_lap_count')
                if isinstance(lc, (int, float)):
                    lap_counts.append(int(lc))

                for k, v in (j or {}).items():
                    if not isinstance(v, (int, float)) or isinstance(v, bool):
                        continue
                    d = por_campo.setdefault(k, {'n': 0, 'por_tipo': {},
                                                 'min': v, 'max': v})
                    d['n'] += 1
                    d['min'] = min(d['min'], v)
                    d['max'] = max(d['max'], v)
                    t = classificadas.get(str(aid))
                    if t:
                        d['por_tipo'][t] = d['por_tipo'].get(t, 0) + 1

            alvo = request.args.get('campo')
            if alvo:
                por_campo = {k: v for k, v in por_campo.items()
                             if k.lower() == alvo.lower()}
            else:
                interesse = ('aet', 'aetwkg', 'aethr', 'mss', 'pbp',
                             'pvo2max', 'hrvt1', 'hrvt1plus', 'hrvt2',
                             'hrvtmss', 'lthrdetected', 'ebp',
                             'fractionalutilizationusing6mpower')
                por_campo = {k: v for k, v in por_campo.items()
                             if k.lower() in interesse}

            return jsonify({
                'status': 'ok',
                'modalidade': modalidade, 'dias': dias,
                'n_actividades': len(linhas),
                'com_laps_no_json': com_laps,
                'sem_laps_no_json': sem_laps,
                'chaves_de_intervalos_encontradas': chaves_intervalos,
                'exemplos_sem_laps': exemplos_sem_laps,
                'amostra_interval_summary': amostra_summary,
                'interval_summary_em_bruto': bruto_summary,
                'todas_as_chaves_do_json': sorted(campos_todos)[:400],
                'lap_count': ({
                    'n': len(lap_counts),
                    'min': min(lap_counts), 'max': max(lap_counts),
                    'com_mais_de_3': sum(1 for x in lap_counts if x > 3),
                } if lap_counts else None),
                'n_classificadas': len(classificadas),
                'tipos_encontrados': {
                    t: sum(1 for x in classificadas.values() if x == t)
                    for t in set(classificadas.values())},
                'campos': {k: {'n_sessoes': v['n'],
                               'min': round(v['min'], 2),
                               'max': round(v['max'], 2),
                               'por_tipo_de_sessao': v['por_tipo']}
                           for k, v in sorted(por_campo.items())},
                'nota': (
                    'passo barato: só usa o JSON já guardado. Se '
                    'com_laps_no_json for baixo, os laps não estão no '
                    'sumário e classificar tudo exigiria uma chamada à API '
                    'por actividade. RESOLVIDO: o interval_summary é uma '
                    'lista de strings ("1x 5m2s 99w") e dá para classificar '
                    'tudo sem chamadas. Não traz WORK/RECOVERY nem '
                    'distância, portanto as recuperações são inferidas pela '
                    'potência e não se distingue intervalado por distância '
                    'de intervalado por tempo'),
            })
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    return app
