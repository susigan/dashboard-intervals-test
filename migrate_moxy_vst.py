"""migrate_moxy_vst.py — migração não-destrutiva de perfil_historico.db
para moxy_vst_historico.db.

Princípio: leitura do banco antigo, UPSERT no banco novo.
Nunca: DROP, DELETE, recriação de tabelas.

Tabelas migradas:
  activity_interval_rpe  → activity_interval_rpe (sem conversão)
  moxy_rpe               → activity_interval_rpe (se t0_s disponível)
  vst_conjuntos (antigo) → vst_conjuntos (novo)
  moxy_analises          → moxy_analyses
"""

import sqlite3
import os
import datetime


def migrar(
    origem="/tmp/perfil_historico.db",
    destino="/tmp/moxy_vst_historico.db",
    dry_run=False
):
    """Migra dados de origem para destino (ambos SQLite).

    dry_run=True: só conta o que seria migrado, sem escrever.
    Retorna dict com contagens.
    """
    if not os.path.exists(origem):
        print(f"[migrate] {origem} nao encontrado — nada a migrar")
        return {}

    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    from moxy_vst_schema import aplicar_schema
    import drive_db_moxy_vst as mvdb

    cn_orig = sqlite3.connect(origem)
    cn_orig.row_factory = sqlite3.Row

    if not dry_run:
        # Usar get_moxy_vst_conn() para garantir download+schema correctos
        # Se não existir no Drive, cria local e continua
        try:
            cn_dest = mvdb.get_moxy_vst_conn()
        except Exception:
            # Fallback: sqlite3 directo com schema manual
            cn_dest = sqlite3.connect(destino)
            aplicar_schema(cn_dest)
    else:
        cn_dest = None

    agora = datetime.datetime.now().isoformat(timespec='seconds')
    contagens = {}

    # ── 1. activity_interval_rpe ──────────────────────────────────
    try:
        rows = cn_orig.execute(
            "SELECT activity_id, start_time, interval_type, elapsed_time, "
            "rpe, source, updated_at FROM activity_interval_rpe"
        ).fetchall()
        contagens['activity_interval_rpe'] = len(rows)
        if not dry_run and rows:
            for r in rows:
                cn_dest.execute(
                    """INSERT INTO activity_interval_rpe
                       (activity_id, start_time, interval_type, elapsed_time,
                        rpe, rpe_status, source, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(activity_id, start_time) DO UPDATE SET
                         rpe        = COALESCE(excluded.rpe, rpe),
                         rpe_status = CASE WHEN excluded.rpe IS NULL THEN rpe_status
                                           ELSE excluded.rpe_status END,
                         source     = CASE WHEN excluded.rpe IS NULL THEN source
                                           ELSE excluded.source END,
                         updated_at = excluded.updated_at
                    """,
                    (r['activity_id'], float(r['start_time']),
                     r['interval_type'], r['elapsed_time'],
                     r['rpe'],
                     'deleted' if r['rpe'] == 0 else 'recorded',
                     r['source'] or 'manual',
                     r['updated_at'] or agora,
                     r['updated_at'] or agora)
                )
            cn_dest.commit()
        print(f"[migrate] activity_interval_rpe: {len(rows)} linhas")
    except Exception as e:
        print(f"[migrate] activity_interval_rpe erro: {e}")

    # ── 2. moxy_rpe → activity_interval_rpe (fallback legado) ─────
    try:
        rows = cn_orig.execute(
            "SELECT activity_id, t0_s, rpe, gravado_em FROM moxy_rpe "
            "WHERE t0_s IS NOT NULL"
        ).fetchall()
        migrados = 0
        if not dry_run and rows:
            for r in rows:
                # Só migrar se não existe linha mais recente em activity_interval_rpe
                existente = cn_dest.execute(
                    "SELECT rpe FROM activity_interval_rpe "
                    "WHERE activity_id=? AND start_time=?",
                    (r['activity_id'], float(r['t0_s']))
                ).fetchone()
                if existente is None:
                    cn_dest.execute(
                        """INSERT OR IGNORE INTO activity_interval_rpe
                           (activity_id, start_time, rpe, rpe_status,
                            source, created_at, updated_at)
                           VALUES (?,?,?,?,?,?,?)
                        """,
                        (r['activity_id'], float(r['t0_s']),
                         r['rpe'], 'recorded', 'moxy_rpe_legacy',
                         r['gravado_em'] or agora, r['gravado_em'] or agora)
                    )
                    migrados += 1
            cn_dest.commit()
        contagens['moxy_rpe_migrados'] = migrados
        print(f"[migrate] moxy_rpe (legado) → activity_interval_rpe: {migrados} novas")
    except Exception as e:
        print(f"[migrate] moxy_rpe erro: {e}")

    # ── 3. vst_conjuntos ──────────────────────────────────────────
    try:
        rows = cn_orig.execute(
            "SELECT vst_activity_id, moxy_activity_id, "
            "       bp1_status, bp2_status, "
            "       dia1_bp1_w, dia2_bp1_w, dia1_bp2_w, dia2_bp2_w, "
            "       recovery_bp1_status, recovery_bp2_status, "
            "       criado_em, actualizado_em "
            "FROM vst_conjuntos"
        ).fetchall()
        contagens['vst_conjuntos'] = len(rows)
        if not dry_run and rows:
            for r in rows:
                cn_dest.execute(
                    """INSERT INTO vst_conjuntos
                       (moxy_activity_id, vst_activity_id,
                        bp1_status, bp2_status,
                        dia1_bp1_w, dia2_bp1_w, dia1_bp2_w, dia2_bp2_w,
                        recovery_bp1_status, recovery_bp2_status,
                        created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(moxy_activity_id, vst_activity_id) DO UPDATE SET
                         bp1_status           = COALESCE(excluded.bp1_status, bp1_status),
                         bp2_status           = COALESCE(excluded.bp2_status, bp2_status),
                         updated_at           = excluded.updated_at
                    """,
                    (r['moxy_activity_id'], r['vst_activity_id'],
                     r['bp1_status'], r['bp2_status'],
                     r['dia1_bp1_w'], r['dia2_bp1_w'],
                     r['dia1_bp2_w'], r['dia2_bp2_w'],
                     r['recovery_bp1_status'], r['recovery_bp2_status'],
                     r['criado_em'] or agora, r['actualizado_em'] or agora)
                )
            cn_dest.commit()
        print(f"[migrate] vst_conjuntos: {len(rows)} linhas")
    except Exception as e:
        print(f"[migrate] vst_conjuntos erro: {e}")

    # ── 4. moxy_analises ──────────────────────────────────────────
    try:
        rows = cn_orig.execute(
            "SELECT activity_id, bp1_w, bp1_bpm, bp2_w, bp2_bpm, "
            "       json_completo, analisado_em "
            "FROM moxy_analises"
        ).fetchall()
        contagens['moxy_analises'] = len(rows)
        if not dry_run and rows:
            for r in rows:
                cn_dest.execute(
                    """INSERT INTO moxy_analyses
                       (activity_id, version, bp1_w, bp1_bpm,
                        bp2_w, bp2_bpm, json_completo,
                        analysis_version, analyzed_at)
                       VALUES (?,1,?,?,?,?,?,?,?)
                       ON CONFLICT(activity_id, version) DO NOTHING
                    """,
                    (r['activity_id'], r['bp1_w'], r['bp1_bpm'],
                     r['bp2_w'], r['bp2_bpm'],
                     r['json_completo'], 'v1',
                     r['analisado_em'] or agora)
                )
            cn_dest.commit()
        print(f"[migrate] moxy_analises: {len(rows)} linhas")
    except Exception as e:
        print(f"[migrate] moxy_analises erro: {e}")

    # ── 5. resultado_json dos vst_conjuntos antigos → vst_results ──
    try:
        rows = cn_orig.execute(
            """SELECT vst_activity_id, moxy_activity_id,
                      resultado_json, validacao_fisiologica_json,
                      bpm_vst_validacao_json, dia1_bp1_w, dia2_bp1_w,
                      dia1_bp2_w, dia2_bp2_w, actualizado_em
               FROM vst_conjuntos
               WHERE resultado_json IS NOT NULL"""
        ).fetchall()
        contagens['vst_results_migrados'] = 0
        if not dry_run and rows:
            for r in rows:
                # Garantir que o conjunto existe no destino
                _moxy_id = r['moxy_activity_id']
                _vst_id  = r['vst_activity_id']
                if _moxy_id and _vst_id:
                    import drive_db_moxy_vst as _mvdb_mig
                    _cj_id = _mvdb_mig.upsert_conjunto(
                        cn_dest, _moxy_id, _vst_id,
                        dia1_bp1_w=r['dia1_bp1_w'], dia2_bp1_w=r['dia2_bp1_w'],
                        dia1_bp2_w=r['dia1_bp2_w'], dia2_bp2_w=r['dia2_bp2_w'])
                    cn_dest.commit()
                    if _cj_id:
                        # Verificar se já existe resultado para este conjunto
                        _existing = cn_dest.execute(
                            "SELECT COUNT(*) FROM vst_results WHERE vst_conjunto_id=?",
                            (_cj_id,)).fetchone()[0]
                        if _existing == 0:
                            _mvdb_mig.insert_resultado(
                                cn_dest, _cj_id,
                                resultado_json=r['resultado_json'],
                                validacao_fisiologica_json=r['validacao_fisiologica_json'],
                                bpm_vst_validacao_json=r['bpm_vst_validacao_json'],
                                bp1_w=r['dia1_bp1_w'],
                                bp2_w=r['dia1_bp2_w'],
                                analysis_version='legacy')
                            cn_dest.commit()
                            contagens['vst_results_migrados'] += 1
        print(f"[migrate] vst_results: {contagens.get('vst_results_migrados', 0)} novos")
    except Exception as e:
        print(f"[migrate] vst_results erro: {e}")

    cn_orig.close()
    if cn_dest:
        cn_dest.close()

    print(f"[migrate] concluido {'(dry_run)' if dry_run else ''}")
    return contagens


if __name__ == "__main__":
    import sys
    dry = '--dry-run' in sys.argv
    origem  = next((a for a in sys.argv[1:] if not a.startswith('--')), None)
    migrar(origem or "/tmp/perfil_historico.db",
           "/tmp/moxy_vst_historico.db", dry_run=dry)
