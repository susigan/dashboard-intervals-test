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
