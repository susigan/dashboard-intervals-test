"""api_moxy.py — sessoes com sensor NIRS (Moxy).

Encontra as sessoes marcadas como Moxy e devolve os streams de SmO2 e THb
ja' limpos pelo pipeline do utils/mnirs.py.

A marca e' procurada no nome, na descricao e nos campos de texto do JSON da
actividade, aceitando 'moxy', '#moxy', 'Moxy' e variantes. Nao se assume
um campo de tags: a Intervals.icu nao expoe um consistentemente, e ja'
custou caro neste projecto assumir nomes de campos.

Registado com:  import api_moxy; api_moxy.registar(app)
"""

import json
import re
import traceback
from datetime import datetime, timedelta

from flask import jsonify, request

PADRAO_MOXY = re.compile(r'(^|[\s#,;/\-_])moxy', re.IGNORECASE)

# Nomes possiveis dos streams NIRS. A Intervals.icu expoe smo2/thb, mas
# ficheiros com dois sensores acrescentam sufixos.
CANAIS = {
    'smo2': ['smo2', 'SmO2', 'smo2_1', 'Smo2'],
    'thb': ['thb', 'THb', 'thb_1'],
    'o2hb': ['O2Hb', 'o2hb'],
    'hhb': ['HHb', 'hhb', 'DiffHb'],
}


def _tem_marca(j, nome):
    alvos = [nome or '', (j or {}).get('description') or '',
             (j or {}).get('name') or ''] + [
        str(v) for k, v in (j or {}).items()
        if 'tag' in k.lower() and isinstance(v, (str, list))]
    return any(PADRAO_MOXY.search(str(a)) for a in alvos)


def registar(app):

    @app.route('/api/moxy/sessoes')
    def api_moxy_sessoes():
        """Sessoes marcadas como Moxy.  ?modalidade=Bike&dias=365&n=50"""
        try:
            import db as _db
            from config import TYPE_MAP
            dias = request.args.get('dias', type=int) or 365
            n = min(request.args.get('n', type=int) or 50, 300)
            modalidade = request.args.get('modalidade')
            corte = (datetime.now() - timedelta(days=dias)).strftime('%Y-%m-%d')

            cond, args = ["raw IS NOT NULL", "date >= ?"], [corte]
            if modalidade:
                variantes = [k for k, v in TYPE_MAP.items() if v == modalidade]
                if variantes:
                    cond.append(f"type IN ({','.join('?' * len(variantes))})")
                    args += variantes
            linhas = _db._exec(
                f"""SELECT id, type, date, name, raw FROM activities
                     WHERE {' AND '.join(cond)} ORDER BY date DESC""",
                tuple(args), fetch='all') or []

            fora, sem_marca = [], 0
            for aid, tipo, data, nome, raw in linhas:
                try:
                    j = raw if isinstance(raw, dict) else json.loads(raw)
                except Exception:
                    continue
                # com marca no texto, OU com Smo2 no sumario (o sensor
                # esteve ligado mesmo sem ninguem ter escrito a tag)
                marca = _tem_marca(j, nome)
                tem_smo2 = (j or {}).get('Smo2') is not None or \
                           (j or {}).get('smo2') is not None
                if not marca and not tem_smo2:
                    sem_marca += 1
                    continue
                fora.append({
                    'id': aid, 'tipo': tipo, 'modalidade': TYPE_MAP.get(tipo),
                    'data': str(data)[:10], 'nome': (nome or '')[:70],
                    'marca_no_texto': marca,
                    'smo2_no_sumario': (j or {}).get('Smo2'),
                    'duracao_min': (round((j or {}).get('moving_time', 0) / 60)
                                    if (j or {}).get('moving_time') else None),
                    'streams': f'/api/moxy/dados/{aid}',
                })
                if len(fora) >= n:
                    break
            return jsonify({
                'status': 'ok', 'n': len(fora), 'dias': dias,
                'modalidade': modalidade,
                'sessoes_sem_marca': sem_marca,
                'sessoes': fora,
                'ultima': fora[0] if fora else None,
                'nota': ('a marca e procurada no nome, descricao e campos de '
                         'tags; sessoes com Smo2 no sumario entram mesmo sem '
                         'marca escrita, porque o sensor esteve ligado')})
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    @app.route('/api/moxy/dados/<path:activity_id>')
    def api_moxy_dados(activity_id):
        """Streams NIRS limpos de uma sessao.

        ?fc=0.02  frequencia de corte   ?outlier=3   ?acima=95
        ?normalizar=deslocar|reescalar
        """
        try:
            import os as _os
            import sys as _sys
            _sys.path.insert(0, _os.path.join(
                _os.path.dirname(_os.path.abspath(__file__)), 'utils'))
            import mnirs as mn
            import api_client as api

            aid = str(activity_id).strip().strip('/').split('/')[-1]
            bruto, err = api.icu_get(f'/activity/{aid}/streams')
            if err:
                return jsonify({'status': 'erro', 'mensagem': f'API: {err}',
                                'id_usado': aid}), 200
            lista = bruto
            if isinstance(lista, dict):
                lista = lista.get('streams') or lista.get('content') or []

            streams = {}
            for st in (lista or []):
                if isinstance(st, dict) and (st.get('type') or st.get('name')):
                    streams[st.get('type') or st.get('name')] = st.get('data') or []

            def _achar(nomes):
                for a in nomes:
                    for k in streams:
                        if k.lower() == a.lower():
                            return k
                return None

            canais, mapa = {}, {}
            for alvo, nomes in CANAIS.items():
                k = _achar(nomes)
                if k and streams[k]:
                    canais[alvo] = streams[k]
                    mapa[alvo] = k
            if not canais:
                return jsonify({
                    'status': 'sem_dados', 'id': aid,
                    'mensagem': 'sem streams de SmO2 ou THb nesta sessao',
                    'streams_na_actividade': sorted(streams)}), 200

            n = len(next(iter(canais.values())))
            tempo = streams.get('time') or list(range(n))

            res = mn.processar(
                tempo, canais, hz=1.0,
                acima=request.args.get('acima', type=float) or 95.0,
                corte_outlier=request.args.get('outlier', type=float) or 3.0,
                fc=request.args.get('fc', type=float) or 0.02,
                normalizar=request.args.get('normalizar'))

            # contexto: potencia e FC para se ver a que intensidade
            for extra in ('watts', 'heartrate', 'cadence'):
                k = _achar([extra])
                if k:
                    t2, v2 = mn.resample(tempo, streams[k], hz=1.0)
                    res['canais'][extra] = [
                        round(x, 1) if x is not None else None for x in v2]

            res['status'] = 'ok'
            res['activity_id'] = aid
            res['streams_usados'] = mapa
            res['streams_na_actividade'] = sorted(streams)
            return jsonify(res)
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    return app
