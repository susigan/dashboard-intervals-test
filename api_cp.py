"""api_cp.py — endpoints de Critical Power e do historico do perfil.

Vive fora do app.py para o manter navegavel. Registado com
    import api_cp; api_cp.registar(app)

Fonte dos MMP: tabela power_curves, sincronizada da API da Intervals.icu.
Nada aqui vem da Google Sheet -- a Sheet so' alimenta wellness e composicao
corporal (peso e %BF), que entram no perfil metabolico como entrada
antropometrica, nao como medida fisiologica.
"""

import os
import sys
import json
import traceback
from datetime import datetime

from flask import jsonify, request

_UTILS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'utils')


def _mods():
    """Mapa tipo-da-API -> modalidade. No config chama-se TYPE_MAP; o
    app.py importa-o como CFG_MODALIDADES, dai o engano inicial."""
    from config import TYPE_MAP
    return TYPE_MAP


def _variantes(modalidade):
    return [k for k, v in _mods().items() if v == modalidade]


def _registos(modalidade):
    """power_curves da modalidade, com a season de cada uma."""
    import db as _db
    from config import season_de
    try:
        from api_client import seasons_do_atleta
        marcos = seasons_do_atleta() or []
    except Exception:
        marcos = []
    variantes = _variantes(modalidade)
    if not variantes:
        return None, None, marcos
    linhas = _db._exec(
        f"""SELECT date, secs, watts FROM power_curves
             WHERE type IN ({','.join('?' * len(variantes))})
             ORDER BY date DESC""", tuple(variantes), fetch='all') or []
    registos = [{'date': str(d)[:10] if d else None, 'secs': s, 'watts': w,
                 'season': season_de(str(d)[:10] if d else None, marcos)}
                for d, s, w in linhas]
    return registos, linhas, marcos


def registar(app):

    # ── CP ────────────────────────────────────────────────────────────────

    @app.route('/api/cp/modelos/<modalidade>')
    def api_cp_modelos(modalidade):
        """Todos os modelos de CP para a modalidade, com SEE% e W'.

        ?season=  (default: activa)   ?min_pts=3   ?limiar_max=0.85
        """
        try:
            sys.path.insert(0, _UTILS)
            import cp_model as cpm
            from config import season_de

            if not _variantes(modalidade):
                return jsonify({'status': 'erro',
                                'mensagem': f'modalidade desconhecida: {modalidade}'}), 400
            registos, _linhas, marcos = _registos(modalidade)
            if not registos:
                return jsonify({'status': 'sem_dados',
                                'mensagem': f'sem power_curves para {modalidade}'}), 200

            hoje = datetime.now().strftime('%Y-%m-%d')
            season = request.args.get('season') or season_de(hoje, marcos)
            min_pts = request.args.get('min_pts', type=int) or 3

            dados = cpm.pontos_de_curvas(
                registos, modalidade, season_activa=season,
                limiar_max=request.args.get('limiar_max', type=float))
            res = cpm.calcular_cp_completo(dados, modalidade, min_pts=min_pts)
            res['status'] = 'ok'
            res['season'] = season
            res['seasons_disponiveis'] = dados.get('seasons_disponiveis')
            res['datas_dos_mmp'] = dados.get('datas')
            res['seasons_dos_mmp'] = dados.get('seasons')
            res['recuou_de_season'] = dados.get('recuou')
            res['tem_calculadora_c2'] = modalidade in cpm.MODALIDADES_C2

            # curvas para desenhar
            res['curvas'] = {
                nome: cpm.curva_do_modelo(m['cp'], m['wp'])
                for nome, m in (res.get('modelos') or {}).items()
                if m.get('cp') and m.get('wp')}
            res['nota_see'] = (
                'SEE% e o erro padrao do ajuste em percentagem da potencia '
                'media: mede se a curva passa perto dos pontos, nao se o CP '
                'esta certo. Modelos de 3 parametros tem mais liberdade e '
                'tendem a ajustar melhor, por isso comparar SEE% entre '
                'modelos com k diferente e enviesado -- a escolha fica '
                'contigo, e o MMP60 serve de validacao externa por nao '
                'entrar em nenhum ajuste.')
            return jsonify(res)
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    @app.route('/api/cp/c2')
    def api_cp_c2():
        """Calculadora Concept2 — so' Row e Ski.  ?w2k=250&w60seg=370&split=2:00.0"""
        try:
            sys.path.insert(0, _UTILS)
            import cp_model as cpm
            w2k = request.args.get('w2k', type=float)
            medidos = {}
            for teste, chave in (('Power Peak', 'wpp'), ('60seg', 'w60seg'),
                                 ('2km', 'w2k'), ('6km', 'w6k'),
                                 ('60min', 'w60min')):
                v = request.args.get(chave, type=float)
                if v:
                    medidos[teste] = v
            split = request.args.get('split')
            return jsonify({
                'status': 'ok',
                'tabela': cpm.tabela_c2(w2k, medidos) if w2k else [],
                'pct_referencia': cpm.PCT_C2,
                'watts_do_split': cpm.watts_de_split(split) if split else None,
                'nota': ('Percentagens do 2 km vindas da tabela de '
                         'equivalencias do ergometro, nao dos teus dados: '
                         'servem para ver a forma do perfil (velocista vs '
                         'diesel), nao para prescrever. Split pela formula '
                         'do Concept2, P = 2.8 / pace^3.')})
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    # ── historico ─────────────────────────────────────────────────────────

    @app.route('/api/perfil/guardar', methods=['POST'])
    def api_perfil_guardar():
        """Grava um instantaneo de CP e/ou perfil metabolico.

        Corpo JSON:
          modalidade        obrigatorio
          season            default: activa
          data_referencia   default: hoje -- a data A QUE o instantaneo diz
                            respeito, escolhida por quem grava
          modelo_cp         nome do modelo de CP a fixar (opcional)
          guardar           ['cp', 'perfil', 'limiares'] (default: todos)
          nota              texto livre
        """
        try:
            sys.path.insert(0, _UTILS)
            import cp_model as cpm
            import perfil_metabolico as pmet
            import drive_db_perfil as ddp
            from config import season_de
            from app import perfil_metabolico_dados

            corpo = request.get_json(silent=True) or {}
            modalidade = corpo.get('modalidade')
            if not modalidade or not _variantes(modalidade):
                return jsonify({'status': 'erro',
                                'mensagem': 'modalidade em falta ou desconhecida'}), 400

            registos, _l, marcos = _registos(modalidade)
            hoje = datetime.now().strftime('%Y-%m-%d')
            season = corpo.get('season') or season_de(hoje, marcos)
            data_ref = (corpo.get('data_referencia') or hoje)[:10]
            agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            quais = corpo.get('guardar') or ['cp', 'perfil', 'limiares']

            conn = ddp.get_conn()
            escrito = {}

            cp_w = wp_j = None
            if 'cp' in quais and registos:
                dados = cpm.pontos_de_curvas(registos, modalidade,
                                             season_activa=season)
                res = cpm.calcular_cp_completo(dados, modalidade)
                escolhido = corpo.get('modelo_cp') or (
                    (res.get('melhor') or {}).get('nome'))
                m = (res.get('modelos') or {}).get(escolhido) or {}
                cp_w, wp_j = m.get('cp'), m.get('wp')
                conn.execute(
                    """INSERT OR REPLACE INTO cp_resultados
                       (data_referencia, data_gravacao, modalidade, season,
                        modelo_escolhido, cp_w, wp_j, see_pct, n_pts,
                        k_params, pmax_w, mmp60_validacao_w, mmp_pts_json,
                        modelos_json, veloclinic_json, origem, nota)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (data_ref, agora, modalidade, season, escolhido,
                     cp_w, wp_j, m.get('see_pct'), m.get('n_pts'),
                     m.get('k_params'), res.get('pmax'), res.get('mmp60_val'),
                     json.dumps(res.get('mmp_pts_full') or []),
                     json.dumps(res.get('modelos') or {}),
                     json.dumps(res.get('veloclinic') or {}),
                     'power_curves / Intervals.icu', corpo.get('nota')))
                escrito['cp'] = {'modelo': escolhido, 'cp_w': cp_w}

            perfil = None
            if 'perfil' in quais or 'limiares' in quais:
                perfil, _cod = perfil_metabolico_dados(modalidade,
                                                       {'season': season})

            if 'perfil' in quais and perfil and perfil.get('status') == 'ok':
                lim = perfil.get('limiares') or {}
                mad = perfil.get('mader') or {}
                ent = perfil.get('entradas') or {}
                avisos = ' | '.join(
                    v for k, v in perfil.items()
                    if k.startswith('aviso') and isinstance(v, str))
                conn.execute(
                    """INSERT OR REPLACE INTO perfil_snapshots
                       (data_referencia, data_gravacao, modalidade, season,
                        vo2max, vlamax, lt1_w, lt1_convencao_w, lt2_w,
                        mlss_w, fatmax_w, pvo2max_w, frac_utilizacao_pct,
                        cp_w, wp_j, peso_kg, bf_pct, mmp_json, zonas_json,
                        entradas_json, avisos, origem)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (data_ref, agora, modalidade, season,
                     perfil.get('vo2max'), perfil.get('vlamax'),
                     lim.get('lt1_w'), lim.get('lt1_convencao_w'),
                     lim.get('lt2_w'), mad.get('mlss_at_w'),
                     mad.get('fatmax_w'), mad.get('pvo2max_w'),
                     mad.get('fractional_utilization_pct'),
                     cp_w, wp_j, ent.get('peso'), ent.get('bf_pct'),
                     json.dumps(perfil.get('mmp_usados') or {}),
                     json.dumps(perfil.get('zonas') or []),
                     json.dumps(ent), avisos or None,
                     'power_curves / Intervals.icu + peso dos Sheets'))
                escrito['perfil'] = {'lt1_w': lim.get('lt1_w'),
                                     'mlss_w': mad.get('mlss_at_w')}

            if 'limiares' in quais:
                from app import limiares_externos_dados
                ext = limiares_externos_dados(modalidade, {'season': season})
                n = 0
                for c in (ext or {}).get('campos', []):
                    q = c.get('quartis') or {}
                    conn.execute(
                        """INSERT OR REPLACE INTO limiares_snapshots
                           (data_referencia, data_gravacao, modalidade, season,
                            campo, grupo, unidade, n, p25, p50, p75, minimo,
                            maximo, watts_equivalente, hr_equivalente,
                            constante)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (data_ref, agora, modalidade, season, c.get('rotulo'),
                         c.get('grupo'), c.get('unidade'), q.get('n'),
                         q.get('p25'), q.get('p50'), q.get('p75'),
                         q.get('min'), q.get('max'),
                         c.get('watts_equivalente'), c.get('hr_equivalente'),
                         1 if c.get('constante') else 0))
                    n += 1
                escrito['limiares'] = {'campos': n}

            conn.commit()
            ok, detalhe = ddp.upload()
            conn.close()
            return jsonify({'status': 'ok' if ok else 'gravado_sem_upload',
                            'data_referencia': data_ref,
                            'data_gravacao': agora,
                            'modalidade': modalidade, 'season': season,
                            'escrito': escrito, 'drive': detalhe})
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    @app.route('/api/perfil/historico/<modalidade>')
    def api_perfil_historico(modalidade):
        """Serie temporal dos instantaneos.  ?de=2025-01-01&ate=2026-12-31"""
        try:
            import drive_db_perfil as ddp
            de = request.args.get('de') or '1900-01-01'
            ate = request.args.get('ate') or '2999-12-31'
            conn = ddp.get_conn()

            def _linhas(sql):
                cur = conn.execute(sql, (modalidade, de, ate))
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchall()]

            perfil = _linhas(
                """SELECT * FROM perfil_snapshots
                    WHERE modalidade = ? AND data_referencia BETWEEN ? AND ?
                    ORDER BY data_referencia""")
            cp = _linhas(
                """SELECT id, data_referencia, data_gravacao, season,
                          modelo_escolhido, cp_w, wp_j, see_pct, n_pts,
                          k_params, pmax_w, mmp60_validacao_w
                     FROM cp_resultados
                    WHERE modalidade = ? AND data_referencia BETWEEN ? AND ?
                    ORDER BY data_referencia""")
            lim = _linhas(
                """SELECT * FROM limiares_snapshots
                    WHERE modalidade = ? AND data_referencia BETWEEN ? AND ?
                    ORDER BY data_referencia, campo""")
            conn.close()

            # intervalos por campo ao longo do tempo
            por_campo = {}
            for r in lim:
                por_campo.setdefault(r['campo'], []).append(
                    {'data': r['data_referencia'], 'p25': r['p25'],
                     'p50': r['p50'], 'p75': r['p75'], 'n': r['n'],
                     'w': r['watts_equivalente']})

            return jsonify({'status': 'ok', 'modalidade': modalidade,
                            'de': de, 'ate': ate,
                            'perfil': perfil, 'cp': cp,
                            'limiares_por_campo': por_campo,
                            'n_instantaneos': len(perfil)})
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    @app.route('/api/perfil/apagar', methods=['POST'])
    def api_perfil_apagar():
        """Apaga um instantaneo. Corpo: modalidade, season, data_referencia."""
        try:
            import drive_db_perfil as ddp
            c = request.get_json(silent=True) or {}
            chave = (c.get('modalidade'), c.get('season'),
                     (c.get('data_referencia') or '')[:10])
            if not all(chave):
                return jsonify({'status': 'erro',
                                'mensagem': 'faltam modalidade, season ou data'}), 400
            conn = ddp.get_conn()
            apagadas = {}
            for t in ('cp_resultados', 'perfil_snapshots', 'limiares_snapshots'):
                cur = conn.execute(
                    f"""DELETE FROM {t} WHERE modalidade = ? AND season = ?
                         AND data_referencia = ?""", chave)
                apagadas[t] = cur.rowcount
            conn.commit()
            ok, detalhe = ddp.upload()
            conn.close()
            return jsonify({'status': 'ok' if ok else 'apagado_sem_upload',
                            'apagadas': apagadas, 'drive': detalhe})
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    @app.route('/api/perfil/diagnostico')
    def api_perfil_diagnostico():
        try:
            import drive_db_perfil as ddp
            return jsonify(ddp.diagnostico())
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    return app
