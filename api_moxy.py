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
        """Sessoes marcadas como Moxy.  ?modalidade=Bike&dias=3650&n=300"""
        try:
            import db as _db
            from config import TYPE_MAP
            # todo o historico por omissao. O limite de 365 dias
            # escondia as sessoes de 2024 e anteriores.
            dias = request.args.get('dias', type=int) or 3650
            n = min(request.args.get('n', type=int) or 300, 500)
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
            # Canais que dependem da cinta peitoral levam o mesmo
            # tratamento que os NIRS. A serie de RR e' o que produz o
            # DFA-a1 e a frequencia respiratoria: se a cinta falha, os tres
            # herdam os buracos, e nas sessoes deste atleta o stream de
            # artefactos chegou a marcar 97% dos pontos. Deixa-los brutos ao
            # lado de um SmO2 filtrado dava a impressao errada de que o
            # ruido era do musculo.
            DA_CINTA = {'heartrate', 'dfa_a1', 'respiration'}
            art_k = _achar(['artifacts', 'artifact'])
            artefactos = streams.get(art_k) if art_k else None
            art_max = request.args.get('artefacto_max', type=float) or 5.0
            n_art = 0

            for alvo, nomes in extras.items():
                k = _achar(nomes)
                if not k:
                    continue
                serie = list(streams[k])
                if alvo in DA_CINTA and artefactos:
                    for i in range(min(len(serie), len(artefactos))):
                        a = artefactos[i]
                        if a is not None and a > art_max:
                            serie[i] = None
                            n_art += 1
                t2, v2 = mn.resample(tempo, serie, hz=1.0)
                if alvo in DA_CINTA:
                    v2, d_rep = mn.replace(
                        v2, invalidos=(0,),
                        corte_outlier=request.args.get('outlier', type=float) or 3.0,
                        largura=15)
                    v2 = mn.media_movel(v2, 15)
                    res['diagnostico'][alvo] = {
                        **d_rep, 'n_pontos': len(v2),
                        'filtro': {'metodo': 'media movel', 'largura': 15},
                        'fonte': 'cinta peitoral'}
                res['canais'][alvo] = [
                    round(x, 2) if x is not None else None for x in v2]
                mapa[alvo] = k
                if alvo not in res['diagnostico']:
                    vv = [x for x in v2 if x is not None]
                    res['diagnostico'][alvo] = {
                        'n_pontos': len(v2),
                        'invalidos': sum(1 for x in v2 if x is None),
                        'outliers': 0, 'pct_substituido': None,
                        'minimo': round(min(vv), 1) if vv else None,
                        'maximo': round(max(vv), 1) if vv else None,
                        'filtro': {'metodo': 'nenhum',
                                   'motivo': 'canal de contexto, so reamostrado'},
                        'fonte': 'stream original'}

            if artefactos:
                validos = [a for a in artefactos if a is not None]
                res['artefactos'] = {
                    'stream': art_k, 'limiar_usado': art_max,
                    'pontos_descartados': n_art,
                    'pct_acima_do_limiar': (
                        round(sum(1 for a in validos if a > art_max)
                              / len(validos) * 100, 1) if validos else None),
                    'nota': ('aplicado so aos canais que vem da cinta '
                             '(FC, DFA-a1, respiracao). O SmO2 e o THb vem '
                             'do Moxy e nao sao afectados')}

            # blocos ON/OFF e proposta de corte
            wt = res['canais'].get('watts')
            if wt:
                bl = mn.detectar_blocos(res['tempo'], wt, hz=1.0)
                res['blocos'] = bl
                res['corte_proposto'] = mn.propor_corte(bl)
            else:
                res['blocos'] = {'ok': False, 'motivo': 'sem potencia'}
                res['corte_proposto'] = {'ok': False, 'motivo': 'sem potencia'}

            # corte gravado pelo utilizador tem prioridade
            try:
                import drive_db_perfil as ddp
                cn = ddp.get_conn()
                r = cn.execute(
                    """SELECT inicio_s, fim_s, origem, nota, data_gravacao
                         FROM moxy_cortes WHERE activity_id = ?""",
                    (aid,)).fetchone()
                cn.close()
                if r:
                    res['corte_guardado'] = {
                        'inicio_s': r[0], 'fim_s': r[1], 'origem': r[2],
                        'nota': r[3], 'data_gravacao': r[4]}
            except Exception as e:
                res['corte_guardado'] = None
                res['erro_corte'] = f'{type(e).__name__}: {e}'

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

    @app.route('/api/moxy/corte', methods=['POST'])
    def api_moxy_corte():
        """Grava o intervalo a analisar de uma sessao.

        Corpo: activity_id, inicio_s, fim_s, modalidade, data, nota.
        Gravar de novo a mesma actividade substitui -- a chave e' o id.
        """
        try:
            import drive_db_perfil as ddp
            c = request.get_json(silent=True) or {}
            aid = str(c.get('activity_id') or '').strip()
            if not aid:
                return jsonify({'status': 'erro',
                                'mensagem': 'activity_id em falta'}), 400
            cn = ddp.get_conn()
            cn.execute(
                """INSERT OR REPLACE INTO moxy_cortes
                   (activity_id, modalidade, data, inicio_s, fim_s, origem,
                    proposto_s, nota, data_gravacao)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (aid, c.get('modalidade'), c.get('data'),
                 c.get('inicio_s'), c.get('fim_s'),
                 c.get('origem') or 'utilizador', c.get('proposto_s'),
                 c.get('nota'),
                 datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            cn.commit()
            ok, det = ddp.upload()
            cn.close()
            return jsonify({'status': 'ok' if ok else 'gravado_sem_upload',
                            'activity_id': aid, 'drive': det})
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    return app
