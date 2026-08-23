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

# So' a tag conta. Antes procurava-se tambem no nome e na descricao, e
# aceitavam-se sessoes com Smo2 no sumario mesmo sem tag -- isso trazia
# actividades que nada tinham a ver, porque qualquer sessao com o sensor
# ligado por acaso entrava. A tag e' uma decisao explicita do atleta; o
# nome nao e'.
PADRAO_MOXY = re.compile(r'^\s*#?\s*moxy\s*$', re.IGNORECASE)

# Nomes possiveis dos streams NIRS. A Intervals.icu expoe smo2/thb, mas
# ficheiros com dois sensores acrescentam sufixos.
CANAIS = {
    'smo2': ['smo2', 'SmO2', 'smo2_1', 'Smo2'],
    'thb': ['thb', 'THb', 'thb_1'],
    'o2hb': ['O2Hb', 'o2hb'],
    'hhb': ['HHb', 'hhb', 'DiffHb'],
}


def _tags(j):
    """Lista de tags da actividade. Vem null quando nao ha nenhuma."""
    t = (j or {}).get('tags')
    if isinstance(t, str):
        return [x.strip() for x in t.split(',') if x.strip()]
    return [str(x).strip() for x in (t or []) if x]


def _tem_tag_moxy(j):
    return any(PADRAO_MOXY.match(t) for t in _tags(j))


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

            fora, sem_tag = [], 0
            tags_vistas = {}
            for aid, tipo, data, nome, raw in linhas:
                try:
                    j = raw if isinstance(raw, dict) else json.loads(raw)
                except Exception:
                    continue
                tt = _tags(j)
                for x in tt:
                    tags_vistas[x] = tags_vistas.get(x, 0) + 1
                if not _tem_tag_moxy(j):
                    sem_tag += 1
                    continue
                fora.append({
                    'id': aid, 'tipo': tipo, 'modalidade': TYPE_MAP.get(tipo),
                    'data': str(data)[:10], 'nome': (nome or '')[:70],
                    'tags': tt,
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
                'sessoes_sem_tag': sem_tag,
                'sessoes': fora,
                'ultima': fora[0] if fora else None,
                'tags_existentes': dict(sorted(tags_vistas.items(),
                                               key=lambda kv: -kv[1])),
                'nota': ('so entram actividades com a tag "Moxy". O nome da '
                         'sessao e ignorado, e ter Smo2 no sumario tambem nao '
                         'chega -- a tag e uma decisao explicita, o resto e '
                         'coincidencia. tags_existentes lista o que ha na '
                         'base, para se confirmar a grafia')})
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

            # todos os outros canais que a sessao tenha: entram sem
            # filtragem NIRS, so' reamostrados, para se poder ver o SmO2
            # contra a intensidade, a respiracao ou a cadencia
            extras = {
                'watts': ['watts', 'power'],
                'heartrate': ['heartrate', 'hr'],
                'cadence': ['cadence'],
                'respiration': ['respiration', 'RespirationRateAlphaHRV'],
                'dfa_a1': ['dfa_a1', 'dfaa1'],
                'velocity_smooth': ['velocity_smooth', 'Speed'],
                'torque': ['torque'],
            }
            for alvo, nomes in extras.items():
                k = _achar(nomes)
                if not k:
                    continue
                t2, v2 = mn.resample(tempo, streams[k], hz=1.0)
                res['canais'][alvo] = [
                    round(x, 2) if x is not None else None for x in v2]
                mapa[alvo] = k

            res['status'] = 'ok'
            res['activity_id'] = aid
            res['streams_usados'] = mapa
            res['canais_nirs'] = sorted(canais)
            res['canais_contexto'] = sorted(
                k for k in res['canais'] if k not in canais)
            res['streams_na_actividade'] = sorted(streams)
            return jsonify(res)
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    return app
