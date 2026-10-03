                vs = [serie[i] for i in range(min(len(t), len(serie)))
                      if a <= t[i] <= b and serie[i] is not None]
                if not vs:
                    return None
                return {'n': len(vs), 'media': round(sum(vs) / len(vs), 1),
                        'min': round(min(vs), 1), 'max': round(max(vs), 1)}

            # cada bloco, ON e OFF, com o que lá está
            detalhe = []
            for b in blocos:
                detalhe.append({
                    'tipo': 'TRABALHO' if b.get('on') else 'recuperação',
                    'de_s': round(b['t0']), 'ate_s': round(b['t1']),
                    'duracao_s': round(b['t1'] - b['t0']),
                    'watts_do_lap': b.get('watts_medio'),
                    'watts_do_stream': _stat(wt, b['t0'], b['t1']),
                    'fc': _stat(hr, b['t0'], b['t1']),
                    'smo2': _stat(sm, b['t0'], b['t1']),
                })

            # de onde vem cada BP
            bls = lim.get('bp_moxy_sem_restricao') or {}
            bmx = lim.get('bp_moxy') or {}
            origem = []
            for nome, fonte in (('script Intervals.icu', bls),
                                ('2 degraus por troço', bmx)):
                if fonte.get('bp1_w') is None:
                    continue
                o = {'metodo': nome,
                     'bp1_w': fonte.get('bp1_w'),
                     'bp1_bpm': fonte.get('bp1_bpm'),
                     'bp2_w': fonte.get('bp2_w'),
                     'bp2_bpm': fonte.get('bp2_bpm'),
                     'pontos_usados': fonte.get('pontos'),
                     'fc_descartada': fonte.get('fc_descartada')}
                # a FC do BP é INTERPOLADA entre degraus: mostrar quais
                for chave, w in (('bp1', fonte.get('bp1_w')),
                                 ('bp2', fonte.get('bp2_w'))):
                    if w is None:
                        continue
                    ps = sorted((p for p in (fonte.get('pontos') or [])
                                 if p.get('hr') is not None),
                                key=lambda p: p['watts'])
                    ab = [p for p in ps if p['watts'] <= w]
                    ac = [p for p in ps if p['watts'] > w]
                    o[f'{chave}_fc_interpolada_entre'] = {
                        'abaixo': ab[-1] if ab else None,
                        'acima': ac[0] if ac else None,
                    }
                origem.append(o)

            return jsonify({
                'status': 'ok', 'activity_id': aid,
                'modalidade': lim.get('modalidade'),
                'fc_valida': d.get('fc_valida'),
                'canais_invalidos': d.get('canais_invalidos'),
                'detalhe_invalidos': d.get('detalhe_invalidos'),
                'congelados': d.get('congelados'),
                'artefactos': d.get('artefactos'),
                'corte_usado': [lim.get('corte_inicio_s'),
                                lim.get('corte_fim_s')],
                'blocos': detalhe,
                'origem_dos_breakpoints': origem,
                'blocos_usados_no_ajuste': lim.get('blocos_usados'),
                'fc_global': _stat(hr, t[0] if t else 0,
                                   t[-1] if t else 0),
                'watts_global': _stat(wt, t[0] if t else 0,
                                      t[-1] if t else 0),
                'como_ler': (
                    'watts_do_lap vem da Intervals.icu; watts_do_stream é '
                    'calculado dos dados em bruto. Se diferirem muito num '
                    'bloco de recuperação, o lap está a incluir tempo de '
                    'transição. A FC do BP é INTERPOLADA entre os dois '
                    'degraus vizinhos — se um deles tiver FC errada, o BP '
                    'herda-a'),
            })
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
