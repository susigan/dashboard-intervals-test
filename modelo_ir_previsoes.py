"""Etapa 16: registo PROSPETIVO e append-only das previsoes da sombra IR e dos resultados observados depois.

Porque existe: a sombra e recalculada a cada pedido e nada guarda, por alvo, o que o modelo previa ANTES de
conhecer o resultado. Este modulo guarda essa previsao com o instante real em que foi calculada.

Duas tabelas (ambas so INSERT; nunca UPDATE nem DELETE):
  * modelo_ir_sombra_previsoes: uma linha por previsao. Identidade = hash das entradas (idempotente: a mesma
    previsao com as mesmas entradas nao se duplica; entradas novas geram linha NOVA, a antiga fica).
  * modelo_ir_sombra_resultados: resultados observados associados depois, em versoes. Uma nova versao nunca
    altera a previsao nem a versao anterior.

Regras (todas verificadas em codigo e em testes):
  * data_alvo > dia de calculo: o alvo nao pode ja estar contido nas entradas (sem informacao futura).
  * resultado observado >= instante de calculo da previsao (sem resultado anterior a previsao) e
    >= dia do alvo (sem resultado antes do teste).
  * previsao bloqueada (historico insuficiente) e registada com previsao_ir NULL e motivo: a ausencia fica explicita.
  * consultas 'as of': previsoes com calculado_em <= instante; resultados com registado_em <= instante.
  * nunca escreve em modelo_ir_ajustes nem em ultimo_ajuste_ir. Nunca promove. Nao e chamado pelo sync (ver relatorio).

Mesmo SQL em SQLite e PostgreSQL (TEXT, DOUBLE PRECISION, INTEGER; PK e UNIQUE compostas).
"""
import hashlib
import json
import math
from datetime import datetime, timezone

from modelo_ir_sombra import sql_para

TAB_PREV = 'modelo_ir_sombra_previsoes'
TAB_RES = 'modelo_ir_sombra_resultados'
MODO = 'conhecido_na_epoca'
ESTADOS_TEMPORAIS = ('disponivel', 'incompleto', 'vazio', 'indisponivel')
ESTADOS_SEM_PREVISAO = ('vazio', 'indisponivel')

COLS_PREV = ['previsao_id', 'calculado_em', 'data_alvo', 'modalidade', 'duracao_s', 'modo', 'versao_modelo',
             'lag_dias', 'previsao_ir', 'parametros', 'entradas_sig', 'estado_temporal', 'motivo_bloqueio']
COLS_RES = ['previsao_id', 'versao', 'resultado_sig', 'test_id', 'y_observado', 'observado_em', 'registado_em']

DDL_PREV = (f"CREATE TABLE IF NOT EXISTS {TAB_PREV} ("
            "previsao_id TEXT NOT NULL PRIMARY KEY, "
            "calculado_em TEXT NOT NULL, "
            "data_alvo TEXT NOT NULL, "
            "modalidade TEXT NOT NULL, "
            "duracao_s INTEGER NOT NULL, "
            "modo TEXT NOT NULL, "
            "versao_modelo TEXT NOT NULL, "
            "lag_dias INTEGER NOT NULL, "
            "previsao_ir DOUBLE PRECISION, "
            "parametros TEXT, "
            "entradas_sig TEXT NOT NULL, "
            "estado_temporal TEXT NOT NULL, "
            "motivo_bloqueio TEXT)")
DDL_RES = (f"CREATE TABLE IF NOT EXISTS {TAB_RES} ("
           "previsao_id TEXT NOT NULL, "
           "versao INTEGER NOT NULL, "
           "resultado_sig TEXT NOT NULL, "
           "test_id TEXT NOT NULL, "
           "y_observado DOUBLE PRECISION NOT NULL, "
           "observado_em TEXT NOT NULL, "
           "registado_em TEXT NOT NULL, "
           "PRIMARY KEY (previsao_id, versao), "
           "UNIQUE (previsao_id, resultado_sig))")
INDICE_PREV = (f"CREATE INDEX IF NOT EXISTS ix_ir_sombra_prev_alvo "
               f"ON {TAB_PREV} (modalidade, duracao_s, data_alvo, calculado_em)")


def _instante(texto):
    """ISO 8601 -> texto UTC sem fuso, com microssegundos (comparavel como texto). Sem fuso = UTC."""
    dt = datetime.fromisoformat(str(texto))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat(timespec='microseconds')


def _dia(texto):
    return str(texto)[:10]


def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False).encode('utf-8')).hexdigest()


def entradas_assinatura(**partes):
    """Hash das entradas usadas na previsao (dados, carga, curvas conhecidas, registo temporal, parametros).
    Cada parte e um valor JSON; qualquer mudanca numa parte muda a assinatura."""
    return _sha(partes)


def previsao_id(modalidade, duracao_s, data_alvo, versao_modelo, entradas_sig, motivo_bloqueio=None):
    """Identidade idempotente: a mesma previsao com as mesmas entradas tem sempre o mesmo id."""
    return _sha({'m': modalidade, 'd': int(duracao_s), 'a': _dia(data_alvo), 'v': versao_modelo,
                 'e': entradas_sig, 'b': motivo_bloqueio or ''})[:32]


def resultado_assinatura(test_id, y_observado):
    return _sha({'t': str(test_id), 'y': round(float(y_observado), 6)})[:32]


class LojaPrevisoes:
    """executor(sql_com_?, params, fetch) -> linhas (o mesmo contrato de modelo_ir_sombra.LojaSombra)."""

    def __init__(self, executor, dialeto):
        self._exec = executor
        self.dialeto = dialeto

    def _q(self, sql):
        return sql_para(self.dialeto, sql)

    def criar(self):
        """Apenas testes. Em producao a tabela so existe apos a migracao revista."""
        for sql in (DDL_PREV, DDL_RES, INDICE_PREV):
            self._exec(sql, (), None)

    def previsao(self, pid):
        row = self._exec(self._q(f"SELECT {', '.join(COLS_PREV)} FROM {TAB_PREV} WHERE previsao_id = ?"),
                         (pid,), 'one')
        return dict(zip(COLS_PREV, row)) if row else None

    def inserir_previsao(self, linha):
        sql = (f"INSERT INTO {TAB_PREV} ({', '.join(COLS_PREV)}) VALUES ({', '.join('?' * len(COLS_PREV))}) "
               "ON CONFLICT DO NOTHING RETURNING previsao_id")
        return self._exec(self._q(sql), tuple(linha[c] for c in COLS_PREV), 'one') is not None

    def versoes(self, pid):
        rows = self._exec(self._q(f"SELECT {', '.join(COLS_RES)} FROM {TAB_RES} WHERE previsao_id = ? "
                                  "ORDER BY versao"), (pid,), 'all') or []
        return [dict(zip(COLS_RES, r)) for r in rows]

    def inserir_resultado(self, linha):
        sql = (f"INSERT INTO {TAB_RES} ({', '.join(COLS_RES)}) VALUES ({', '.join('?' * len(COLS_RES))}) "
               "ON CONFLICT DO NOTHING RETURNING previsao_id")
        return self._exec(self._q(sql), tuple(linha[c] for c in COLS_RES), 'one') is not None

    def tabela_existe(self):
        """Catalogo primeiro: sem a migracao nao ha SELECT que possa falhar nem escrita."""
        try:
            self._exec(f'SELECT 1 FROM {TAB_PREV} WHERE 1=0', (), 'one')
            self._exec(f'SELECT 1 FROM {TAB_RES} WHERE 1=0', (), 'one')
            return True
        except Exception:  # noqa: BLE001
            return False

    def todas(self):
        rows = self._exec(self._q(f"SELECT {', '.join(COLS_PREV)} FROM {TAB_PREV} "
                                  "ORDER BY data_alvo, calculado_em, previsao_id"), (), 'all') or []
        return [dict(zip(COLS_PREV, r)) for r in rows]

    def previsoes_ate(self, data_limite):
        """Previsoes cujo dia-alvo ja chegou (<= data_limite): candidatas a associacao de resultado."""
        rows = self._exec(self._q(f"SELECT {', '.join(COLS_PREV)} FROM {TAB_PREV} WHERE data_alvo <= ? "
                                  "ORDER BY data_alvo, calculado_em, previsao_id"), (_dia(data_limite),), 'all') or []
        return [dict(zip(COLS_PREV, r)) for r in rows]

    def previsoes_do_alvo(self, mod, dur, data_alvo, as_of):
        """Previsoes para (mod, dur, data_alvo) JA CALCULADAS no instante as_of, da mais antiga para a mais recente."""
        rows = self._exec(self._q(
            f"SELECT {', '.join(COLS_PREV)} FROM {TAB_PREV} WHERE modalidade = ? AND duracao_s = ? AND data_alvo = ? "
            "AND calculado_em <= ? ORDER BY calculado_em, previsao_id"),
            (mod, int(dur), _dia(data_alvo), _instante(as_of)), 'all') or []
        return [dict(zip(COLS_PREV, r)) for r in rows]


def registar_previsao(loja, *, calculado_em, data_alvo, modalidade, duracao_s, versao_modelo, lag_dias,
                      entradas_sig, estado_temporal, previsao_ir=None, parametros=None, motivo_bloqueio=None):
    """Regista UMA previsao. Devolve {'acao': 'registada'|'ja_registada'|'concorrente_ja_registou'|'rejeitada', ...}.
    Nunca altera uma previsao existente."""
    try:
        calc = _instante(calculado_em)
        alvo = _dia(data_alvo)
    except (TypeError, ValueError) as e:
        return {'acao': 'rejeitada', 'motivo': f'data_invalida: {e}'}
    if not alvo > calc[:10]:
        return {'acao': 'rejeitada', 'motivo': 'alvo_nao_prospectivo',
                'detalhe': 'a data-alvo tem de ser posterior ao dia do calculo'}
    if estado_temporal not in ESTADOS_TEMPORAIS:
        return {'acao': 'rejeitada', 'motivo': 'estado_temporal_desconhecido'}
    if estado_temporal in ESTADOS_SEM_PREVISAO and not motivo_bloqueio:
        return {'acao': 'rejeitada', 'motivo': 'bloqueio_sem_motivo'}
    if motivo_bloqueio:
        if previsao_ir is not None:
            return {'acao': 'rejeitada', 'motivo': 'previsao_e_bloqueio_em_simultaneo'}
    else:
        if previsao_ir is None or not math.isfinite(float(previsao_ir)):
            return {'acao': 'rejeitada', 'motivo': 'previsao_ir_invalida'}
    pid = previsao_id(modalidade, duracao_s, alvo, versao_modelo, entradas_sig, motivo_bloqueio)
    if loja.previsao(pid) is not None:
        return {'acao': 'ja_registada', 'previsao_id': pid}
    linha = {'previsao_id': pid, 'calculado_em': calc, 'data_alvo': alvo, 'modalidade': modalidade,
             'duracao_s': int(duracao_s), 'modo': MODO, 'versao_modelo': str(versao_modelo),
             'lag_dias': int(lag_dias), 'previsao_ir': None if motivo_bloqueio else float(previsao_ir),
             'parametros': None if parametros is None else json.dumps(parametros, sort_keys=True, default=str),
             'entradas_sig': entradas_sig, 'estado_temporal': estado_temporal,
             'motivo_bloqueio': motivo_bloqueio}
    if loja.inserir_previsao(linha):
        return {'acao': 'registada', 'previsao_id': pid}
    return {'acao': 'concorrente_ja_registou', 'previsao_id': pid}


def associar_resultado(loja, pid, *, test_id, y_observado, observado_em, registado_em=None):
    """Associa um resultado observado a uma previsao JA registada. Nunca altera a previsao.
    Devolve {'acao': 'registado'|'ja_registado'|'concorrente_ja_registou'|'rejeitado', ...}."""
    prev = loja.previsao(pid)
    if prev is None:
        return {'acao': 'rejeitado', 'motivo': 'previsao_inexistente'}
    if prev['previsao_ir'] is None:
        return {'acao': 'rejeitado', 'motivo': 'previsao_bloqueada_sem_valor'}
    try:
        obs = _instante(observado_em)
        reg = _instante(registado_em) if registado_em else _instante(datetime.now(timezone.utc).isoformat())
        y = float(y_observado)
    except (TypeError, ValueError) as e:
        return {'acao': 'rejeitado', 'motivo': f'dados_invalidos: {e}'}
    if not math.isfinite(y):
        return {'acao': 'rejeitado', 'motivo': 'y_invalido'}
    if obs < prev['calculado_em']:
        return {'acao': 'rejeitado', 'motivo': 'resultado_anterior_a_previsao',
                'detalhe': 'o resultado nao pode ser conhecido antes do calculo da previsao (futuro)'}
    if obs[:10] < prev['data_alvo']:
        return {'acao': 'rejeitado', 'motivo': 'resultado_antes_do_alvo'}
    rsig = resultado_assinatura(test_id, y)
    if any(v['resultado_sig'] == rsig for v in loja.versoes(pid)):
        return {'acao': 'ja_registado', 'previsao_id': pid}
    versao = len(loja.versoes(pid)) + 1
    linha = {'previsao_id': pid, 'versao': versao, 'resultado_sig': rsig, 'test_id': str(test_id),
             'y_observado': y, 'observado_em': obs, 'registado_em': reg}
    if loja.inserir_resultado(linha):
        return {'acao': 'registado', 'previsao_id': pid, 'versao': versao}
    return {'acao': 'concorrente_ja_registou', 'previsao_id': pid}


def resultado_conhecido_em(loja, pid, as_of):
    """Ultima versao do resultado REGISTADA ate ao instante as_of (nao a que ocorreu antes). None se nao havia."""
    limite = _instante(as_of)
    visiveis = [v for v in loja.versoes(pid) if v['registado_em'] <= limite]
    return visiveis[-1] if visiveis else None


def loja_da_base():
    """Loja sobre a base operacional, so para leitura/INSERT nas tabelas do registo (mesmo executor do sombra)."""
    import db
    return LojaPrevisoes(lambda sql, params, fetch: db._exec(sql, params, fetch), db.DRIVER)


def loja_sqlite(caminho):
    from modelo_ir_sombra import loja_sqlite as _ls
    base = _ls(caminho)
    return LojaPrevisoes(base._exec, 'sqlite')


def loja_postgres(dsn):
    from modelo_ir_sombra import loja_postgres as _lp
    base = _lp(dsn)
    return LojaPrevisoes(base._exec, 'postgres')
