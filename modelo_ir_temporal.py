"""Registo temporal prospetivo do IR (Etapa 10).

Guarda, com o instante real em que o sistema ficou a conhecer cada coisa, o que nao se conseguia
reconstruir a partir da base atual:

  * ir_marcos_snapshots: cada calendario de marcos (seasons) obtido, e o instante da obtencao.
    (obter_marcos() devolve so o estado atual da Intervals.icu; sem isto o passado nao e recuperavel.)
  * ir_yes_transicoes: cada mudanca do conjunto de YES efetivos por teste (aparece, muda de valor ou de
    atividade, ou deixa de ser YES). modelo_mmp3_yes_historico tem PK (test_id, regra_versao) com
    ON CONFLICT DO NOTHING: nao regista mudancas nem saidas, por isso nao pode ser usada para isto.

Propriedades:
  * append-only: nunca UPDATE nem DELETE. Uma mudanca nova e uma linha nova.
  * idempotente: so grava quando o estado difere do ultimo registado. Repetir a mesma recalibracao
    ou a mesma obtencao de marcos nao duplica linhas.
  * o instante e o da escrita (agora). Nunca se atribui data passada a um registo.
  * as funcoes de escrita devolvem um estado; nao levantam para o fluxo chamador (ver servico).
  * SQL igual em SQLite e PostgreSQL (TEXT, DOUBLE PRECISION, INTEGER; PK compostas).
"""
import hashlib
import json
from datetime import datetime, timezone

from modelo_ir_sombra import sql_para

TAB_MARCOS = 'ir_marcos_snapshots'
TAB_YES = 'ir_yes_transicoes'
ESTADO_YES = 'yes'
ESTADO_DEIXOU = 'deixou_de_ser_yes'
COLS_YES = ['estado', 'modalidade', 'activity_id', 'watts', 'regra_versao', 'origem', 'revisao']

DDL_MARCOS = (f"CREATE TABLE IF NOT EXISTS {TAB_MARCOS} ("
              "conhecido_em TEXT NOT NULL PRIMARY KEY, "
              "hash TEXT NOT NULL, "
              "fonte TEXT NOT NULL, "
              "marcos TEXT NOT NULL)")
DDL_YES = (f"CREATE TABLE IF NOT EXISTS {TAB_YES} ("
           "test_id TEXT NOT NULL, "
           "conhecido_em TEXT NOT NULL, "
           "estado TEXT NOT NULL, "
           "modalidade TEXT NOT NULL, "
           "activity_id TEXT, "
           "watts DOUBLE PRECISION, "
           "regra_versao INTEGER, "
           "origem TEXT, "
           "revisao INTEGER, "
           "PRIMARY KEY (test_id, conhecido_em))")
INDICE_YES = (f"CREATE INDEX IF NOT EXISTS ix_ir_yes_trans_test ON {TAB_YES} (test_id, conhecido_em)")


def instante(agora):
    """ISO com microssegundos (largura fixa: a ordenacao lexicografica e a cronologica)."""
    if isinstance(agora, str):
        agora = datetime.fromisoformat(agora)
    return agora.isoformat(timespec='microseconds')


def _corte_inicio(data):
    """'AAAA-MM-DD' -> instante do inicio desse dia. Um registo so conta para um alvo se for ANTERIOR a ele."""
    return f'{data}T00:00:00.000000'


def como_datetime(v):
    """Normaliza um instante vindo de SQLite (texto ISO) ou PostgreSQL (datetime) para datetime NAIVE em UTC.
    None ou valor ilegivel -> None (o chamador trata como 'nao provado')."""
    if v is None or v == '':
        return None
    try:
        if isinstance(v, datetime):
            dt = v
        else:
            dt = datetime.fromisoformat(str(v).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def instante_prova_inicio(data):
    """'AAAA-MM-DD' -> datetime naive do inicio do dia (para comparar com como_datetime)."""
    return datetime.fromisoformat(f'{data}T00:00:00')


def canonico(marcos):
    if marcos is None:
        return 'null'
    return json.dumps(marcos, ensure_ascii=False, sort_keys=True, default=str)


def hash_marcos(marcos, fonte):
    return hashlib.sha256(f'{fonte}|{canonico(marcos)}'.encode('utf-8')).hexdigest()


def _mesmo_yes(atual, novo):
    if atual is None:
        return False
    for c in COLS_YES:
        a, b = atual.get(c), novo.get(c)
        if c == 'watts':
            a = None if a is None else round(float(a), 6)
            b = None if b is None else round(float(b), 6)
        if a != b:
            return False
    return True


class RegistoTemporal:
    """executor(sql_com_?, params, fetch) -> linhas. fetch in (None, 'one', 'all'). Mesma interface de
    modelo_ir_sombra.LojaSombra, para testar com SQLite e PostgreSQL e usar db._exec em producao."""

    def __init__(self, executor, dialeto):
        self._exec = executor
        self.dialeto = dialeto

    def _q(self, sql):
        return sql_para(self.dialeto, sql)

    def tabelas_existem(self):
        """False se a migracao ainda nao foi aplicada. Nao cria nada e NAO executa SQL que falhe: um erro faz o
        db reconectar-se (e os pedidos de leitura nao podem alterar nada). Consulta ao catalogo."""
        if self.dialeto == 'sqlite':
            sql = "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN (?, ?)"
        else:
            sql = "SELECT table_name FROM information_schema.tables WHERE table_name IN (%s, %s)"
        try:
            rows = self._exec(sql, (TAB_MARCOS, TAB_YES), 'all') or []
        except Exception:  # noqa: BLE001 - sem catalogo legivel = nao provado
            return False
        return len({r[0] for r in rows}) == 2

    # ── marcos da temporada ─────────────────────────────────────────────────

    def registar_marcos(self, marcos, fonte, agora):
        """'gravado' | 'sem_alteracao'. marcos=None (calendario indisponivel) e um estado real e e registado."""
        h = hash_marcos(marcos, fonte)
        ult = self._exec(self._q(f'SELECT hash FROM {TAB_MARCOS} ORDER BY conhecido_em DESC LIMIT 1'),
                         (), 'one')
        if ult and ult[0] == h:
            return 'sem_alteracao'
        self._exec(self._q(f'INSERT INTO {TAB_MARCOS} (conhecido_em, hash, fonte, marcos) VALUES (?, ?, ?, ?) '
                           'ON CONFLICT (conhecido_em) DO NOTHING'),
                   (instante(agora), h, fonte, canonico(marcos)), None)
        return 'gravado'

    def contar(self):
        """(n_snapshots_marcos, n_transicoes_yes). Usado pelo contrato da API ('vazio' vs 'disponivel')."""
        m = self._exec(f'SELECT COUNT(*) FROM {TAB_MARCOS}', (), 'one')
        y = self._exec(f'SELECT COUNT(*) FROM {TAB_YES}', (), 'one')
        return (int(m[0] or 0), int(y[0] or 0))

    def marcos_conhecidos_em(self, data_alvo):
        """Marcos conhecidos ANTES do inicio de data_alvo ('AAAA-MM-DD'). None = nenhum registo anterior
        (rejeicao explicita para o avaliador, nunca substituicao pelo estado atual)."""
        row = self._exec(self._q(f'SELECT conhecido_em, fonte, marcos FROM {TAB_MARCOS} '
                                 'WHERE conhecido_em < ? ORDER BY conhecido_em DESC LIMIT 1'),
                         (_corte_inicio(data_alvo),), 'one')
        if not row:
            return None
        return {'conhecido_em': row[0], 'fonte': row[1], 'marcos': json.loads(row[2])}

    # ── YES efetivos ────────────────────────────────────────────────────────

    def _ultimo_por_teste(self, antes_de=None):
        """Ultimo estado registado por teste (opcionalmente so os registados antes de antes_de).
        Devolve {test_id: {conhecido_em, estado, modalidade, activity_id, watts, regra_versao, origem, revisao}}."""
        cols = ', '.join('t.' + c for c in ['conhecido_em'] + COLS_YES)
        if antes_de is None:
            sql = (f'SELECT t.test_id, {cols} FROM {TAB_YES} t WHERE t.conhecido_em = ('
                   f'SELECT MAX(x.conhecido_em) FROM {TAB_YES} x WHERE x.test_id = t.test_id)')
            params = ()
        else:
            sql = (f'SELECT t.test_id, {cols} FROM {TAB_YES} t WHERE t.conhecido_em < ? AND t.conhecido_em = ('
                   f'SELECT MAX(x.conhecido_em) FROM {TAB_YES} x '
                   f'WHERE x.test_id = t.test_id AND x.conhecido_em < ?)')
            params = (antes_de, antes_de)
        rows = self._exec(self._q(sql), params, 'all') or []
        return {r[0]: dict(zip(['conhecido_em'] + COLS_YES, r[1:])) for r in rows}

    def registar_transicoes_yes(self, efetivos, agora):
        """efetivos: {test_id: {modalidade, activity_id, watts, regra_versao, origem, revisao}} = YES de hoje.
        Grava so as diferencas em relacao ao ultimo estado registado. Devolve o numero de linhas novas."""
        inst = instante(agora)
        atual = self._ultimo_por_teste()
        n = 0
        for tid, e in efetivos.items():
            novo = {'estado': ESTADO_YES, 'modalidade': e['modalidade'], 'activity_id': str(e['activity_id']),
                    'watts': e['watts'], 'regra_versao': e.get('regra_versao'), 'origem': e.get('origem'),
                    'revisao': e.get('revisao')}
            ult = atual.get(tid)
            if _mesmo_yes(ult, novo):
                continue
            self._inserir_yes(tid, inst, novo)
            n += 1
        for tid, ult in atual.items():
            if tid in efetivos or ult['estado'] == ESTADO_DEIXOU:
                continue
            self._inserir_yes(tid, inst, {'estado': ESTADO_DEIXOU, 'modalidade': ult['modalidade'],
                                          'activity_id': None, 'watts': None, 'regra_versao': None,
                                          'origem': None, 'revisao': None})
            n += 1
        return n

    def _inserir_yes(self, tid, inst, l):
        self._exec(self._q(f'INSERT INTO {TAB_YES} (test_id, conhecido_em, estado, modalidade, activity_id, '
                           'watts, regra_versao, origem, revisao) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) '
                           'ON CONFLICT (test_id, conhecido_em) DO NOTHING'),
                   (tid, inst, l['estado'], l['modalidade'], l['activity_id'], l['watts'],
                    l['regra_versao'], l['origem'], l['revisao']), None)

    def yes_conhecidos_em(self, data_alvo):
        """{test_id: estado} dos YES conhecidos ANTES do inicio de data_alvo (so os que eram YES nesse momento)."""
        ult = self._ultimo_por_teste(antes_de=_corte_inicio(data_alvo))
        return {tid: r for tid, r in ult.items() if r['estado'] == ESTADO_YES}

    def assinatura_estado(self):
        """Hash do estado atual do registo (ultimo marco + ultimo estado por teste). Entra na assinatura da
        sombra: se o registo mudar, a avaliacao antiga deixa de ser reutilizada."""
        ult_m = self._exec(self._q(f'SELECT conhecido_em, hash FROM {TAB_MARCOS} '
                                   'ORDER BY conhecido_em DESC LIMIT 1'), (), 'one')
        ult_y = self._ultimo_por_teste()
        conteudo = json.dumps({'marco': list(ult_m) if ult_m else None,
                               'yes': {t: [r['conhecido_em'], r['estado'], r['watts']]
                                       for t, r in sorted(ult_y.items())}},
                              sort_keys=True, default=str)
        return hashlib.sha256(conteudo.encode('utf-8')).hexdigest()


# ── invólucros para o fluxo operacional: nunca levantam ─────────────────────────

def _loja_producao():
    import db
    if not db.ENABLED or db.DRIVER is None:
        return None
    return RegistoTemporal(db._exec, db.DRIVER)


def registar_marcos_seguro(marcos, fonte, agora):
    """Regista o calendario de marcos obtido. Falhar nao interrompe o fluxo; devolve o estado."""
    try:
        loja = _loja_producao()
        if loja is None:
            return {'estado': 'sem_base'}
        if not loja.tabelas_existem():
            return {'estado': 'tabelas_ausentes'}
        return {'estado': loja.registar_marcos(marcos, fonte, agora)}
    except Exception as e:  # noqa: BLE001 - nunca propagar para a sincronizacao
        return {'estado': 'erro', 'erro': f'{type(e).__name__}: {e}'}


def registar_yes_seguro(efetivos, agora):
    """Regista as transicoes de YES efetivos. Falhar nao interrompe o fluxo; devolve o estado."""
    try:
        loja = _loja_producao()
        if loja is None:
            return {'estado': 'sem_base'}
        if not loja.tabelas_existem():
            return {'estado': 'tabelas_ausentes'}
        return {'estado': 'ok', 'transicoes': loja.registar_transicoes_yes(efetivos, agora)}
    except Exception as e:  # noqa: BLE001
        return {'estado': 'erro', 'erro': f'{type(e).__name__}: {e}'}


def leitor_producao():
    """(RegistoTemporal | None, estado). Estado: 'ok' | 'sem_base' | 'tabelas_ausentes'.
    Nunca levanta. Sem o leitor, o chamador NAO pode apresentar a avaliacao como temporal."""
    try:
        loja = _loja_producao()
        if loja is None:
            return None, 'sem_base'
        if not loja.tabelas_existem():
            return None, 'tabelas_ausentes'
        return loja, 'ok'
    except Exception:  # noqa: BLE001
        return None, 'erro'
