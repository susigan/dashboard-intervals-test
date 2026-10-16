"""Exportacao completa de atividades e streams originais (Intervals.icu).

Fluxo, acionado so por pedido explicito do utilizador:
  1. novo_job()  -> guarda a selecao em disco e inicia um job em segundo plano.
  2. Job.executar() -> GET /activity/{id}/streams (sem parse_streams: sem downsample),
     com concorrencia limitada, intervalo minimo entre pedidos, retry com Retry-After,
     timeouts. Cada resposta bruta vai para um arquivo proprio; o estado vai para
     estado.json, atomico.
  3. montar_pacote() -> ZIP montado a partir dos arquivos, um de cada vez.

Atividades que falham ficam registradas com o motivo; a retomada refaz so essas.
Nada aqui roda na importacao do modulo nem no arranque da aplicacao.
"""

import csv
import email.utils
import hashlib
import json
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import export


def _env(nome, padrao, tipo, minimo, maximo):
    try:
        v = tipo(os.getenv(nome, padrao))
    except (TypeError, ValueError):
        v = tipo(padrao)
    return max(minimo, min(maximo, v))


# Limites configuraveis. Padrao: 2 pedidos em paralelo, 0,5 s entre inicios de pedido
# (120 por minuto, abaixo dos ~166/min de 2500 a cada 15 min citados em sync.py).
CONFIG = {
    'concorrencia': _env('EXPORT_CONCORRENCIA', 2, int, 1, 4),
    'intervalo_s': _env('EXPORT_INTERVALO_S', 0.5, float, 0.0, 10.0),
    'tentativas': _env('EXPORT_TENTATIVAS', 4, int, 1, 10),
    'timeout_s': _env('EXPORT_TIMEOUT_S', 60, float, 5, 300),
    'max_atividades': _env('EXPORT_MAX_ATIVIDADES', 5000, int, 1, 20000),
    'ttl_horas': 24,
    'lote': 50,
}
RETRY_AFTER_PADRAO_S = 60
RETRY_AFTER_MAX_S = 900
DIR_BASE = os.path.join(tempfile.gettempdir(), 'exportacao_jobs')
_ID_JOB = re.compile(r'^[0-9a-f]{24}$')
ESTADOS_PENDENTES = ('pendente',)
ESTADOS_FALHA = ('erro_api', 'erro_formato')

COLUNAS_STREAMS = ['activity_id', 'stream_key', 'stream_type', 'stream_name', 'custom',
                   'sensor_name', 'sample_index', 'time_seconds', 'value', 'unit',
                   'origem', 'pontos_originais', 'reduzido']


class JobEmAndamento(Exception):
    pass


class JobInexistente(Exception):
    pass


# ── HTTP real: unica funcao que chama a API ──────────────────────────────

def buscar_streams_api(aid):
    """GET /activity/{id}/streams com a mesma query da pagina de detalhe.
    Devolve status, Retry-After e corpo JSON. Nao imprime credenciais nem headers."""
    import requests
    from api_client import BASE, AUTH
    r = requests.get(f"{BASE}/activity/{aid}/streams", auth=AUTH,
                     params={"includeDefaults": "true"}, timeout=CONFIG['timeout_s'])
    corpo = None
    if r.status_code == 200:
        try:
            corpo = r.json()
        except ValueError:
            corpo = None
    return {'status': r.status_code, 'retry_after': r.headers.get('Retry-After'), 'corpo': corpo}


def _espera_retry_after(valor):
    if not valor:
        return RETRY_AFTER_PADRAO_S
    try:
        return min(RETRY_AFTER_MAX_S, max(1, int(float(valor))))
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(valor)
        seg = (dt - datetime.now(dt.tzinfo)).total_seconds()
        return min(RETRY_AFTER_MAX_S, max(1, int(seg)))
    except Exception:
        return RETRY_AFTER_PADRAO_S


class Limitador:
    """Intervalo minimo entre inicios de pedido. Relogio e espera injetaveis (testes)."""

    def __init__(self, intervalo_s, relogio=time.monotonic, espera=time.sleep):
        self.intervalo = intervalo_s
        self._relogio = relogio
        self._espera = espera
        self._lock = threading.Lock()
        self._prox = 0.0

    def aguardar(self):
        with self._lock:
            agora = self._relogio()
            inicio = max(agora, self._prox)
            self._prox = inicio + self.intervalo
        if inicio > agora:
            self._espera(inicio - agora)


# ── leitura da resposta ───────────────────────────────────────────────────

def _lista(corpo):
    """Aceita lista direta ou objeto com 'streams'/'content' (as duas formas que o codigo ja usa)."""
    if isinstance(corpo, list):
        return corpo
    if isinstance(corpo, dict):
        for chave in ('streams', 'content'):
            if isinstance(corpo.get(chave), list):
                return corpo[chave]
    return None


def _e_numerico(v):
    return v is None or (isinstance(v, (int, float)) and not isinstance(v, bool))


def _classificar(s):
    """'numerico' quando o CSV representa o stream sem perda; 'estruturado' quando nao;
    'sem_dados' quando a resposta nao trouxe 'data'."""
    if not isinstance(s, dict):
        return 'estruturado'
    if any(isinstance(v, list) for k, v in s.items() if k != 'data'):
        return 'estruturado'
    data = s.get('data')
    if data is None:
        return 'sem_dados'
    if not isinstance(data, list):
        return 'estruturado'
    return 'numerico' if all(_e_numerico(v) for v in data) else 'estruturado'


def _processar_resposta(aid, lista, origem):
    """Gera as linhas de streams.csv (generator), os objetos de metadados e os estruturados.

    Nada e reduzido, reordenado ou preenchido. Nome de chave duplicado recebe sufixo #n.
    origem 'original_api' ou 'local_reduzido' (o banco guarda so a serie reduzida)."""
    itens, chaves, tempo, observadas = [], set(), None, set()
    for i, s in enumerate(lista):
        tipo = (s.get('type') or s.get('name')) if isinstance(s, dict) else None
        tipo = tipo or f'sem_tipo_{i}'
        chave, n = tipo, 2
        while chave in chaves:
            chave = f'{tipo}#{n}'
            n += 1
        chaves.add(chave)
        cls = _classificar(s) if origem == 'original_api' else 'numerico'
        if isinstance(s, dict):
            observadas.update(s.keys())
        itens.append((chave, tipo, cls, s))
        if cls == 'numerico' and tipo == 'time' and isinstance(s.get('data'), list):
            tempo = s['data']

    metas, estruturados = [], []
    linhas_nome = []   # tuplas (chave, tipo, s, cls) para o gerador
    for chave, tipo, cls, s in itens:
        if cls == 'estruturado':
            estruturados.append({'activity_id': aid, 'stream_key': chave, 'origem': origem,
                                 'stream': s})
            continue
        meta = {k: v for k, v in s.items() if k != 'data'}
        meta['stream_key'] = chave
        meta['origem'] = origem
        metas.append({'activity_id': aid, 'stream': meta})
        if cls == 'numerico':
            linhas_nome.append((chave, tipo, s))

    def linhas():
        for chave, tipo, s in linhas_nome:
            data = s.get('data')
            if origem == 'original_api':
                pontos = len(data)
                reduz = 'false'
            else:
                pontos = s.get('points') if isinstance(s.get('points'), int) else None
                reduz = 'true' if pontos is not None and pontos > len(data) else (
                    'false' if pontos is not None else '')
            usa_tempo = tempo is not None and len(tempo) == len(data)
            for i, v in enumerate(data):
                if tipo == 'time':
                    t = v
                elif usa_tempo:
                    t = tempo[i]
                else:
                    t = None
                yield {
                    'activity_id': export.celula(aid),
                    'stream_key': export.celula(chave),
                    'stream_type': export.celula(tipo),
                    'stream_name': export.celula(s.get('name')),
                    'custom': export.celula(s.get('custom')) if 'custom' in s else '',
                    'sensor_name': export.celula(s.get('sensor_name') or s.get('name')),
                    'sample_index': i,
                    'time_seconds': export.celula(t) if t is not None else '',
                    'value': export.celula(v),
                    'unit': export.celula(s.get('unit')) if s.get('unit') is not None else '',
                    'origem': origem,
                    'pontos_originais': pontos if pontos is not None else '',
                    'reduzido': reduz,
                }

    resumo = {
        'tipos_numericos': sorted({t for _, t, _ in linhas_nome}),
        'tipos_estruturados': sorted({e['stream'].get('type') or e['stream_key']
                                      for e in estruturados if isinstance(e['stream'], dict)}),
        'amostras': sum(len(s.get('data') or []) for _, _, s in linhas_nome),
        'chaves_observadas': sorted(observadas),
        'streams': len(itens),
    }
    return linhas, metas, estruturados, resumo


# ── estado persistido do job ──────────────────────────────────────────────

class Job:
    def __init__(self, job_id, dir_, ids, filtros, buscar=None, espera=None,
                 relogio=None, local=None, salvar_intervalo_s=5.0):
        self.id = job_id
        self.dir = dir_
        self.ordem = list(ids)
        self.filtros = filtros
        self.buscar = buscar or buscar_streams_api
        self.local = local or _local_padrao
        self._espera = espera or time.sleep
        self._relogio = relogio or time.monotonic
        self.limitador = Limitador(CONFIG['intervalo_s'], self._relogio, self._espera)
        self._sem = threading.BoundedSemaphore(CONFIG['concorrencia'])
        self._lock = threading.RLock()
        self._salvar_intervalo = salvar_intervalo_s
        self._ultimo_salvar = 0.0
        self.rodando = False
        self.chamadas = 0
        self.em_voo = 0
        self.pico_em_voo = 0
        self.estado = {aid: {'status': 'pendente', 'tentativas': 0, 'motivo': None,
                             'http': None, 'origem': None, 'arquivo': None,
                             'fallback_local': False, 'streams_locais': 0}
                       for aid in self.ordem}
        self.acts = {}
        self.criado_em = datetime.now().isoformat(timespec='seconds')

    # persistencia
    def _caminho_estado(self):
        return os.path.join(self.dir, 'estado.json')

    def _salvar(self, forcar=False):
        with self._lock:
            agora = self._relogio()
            if not forcar and agora - self._ultimo_salvar < self._salvar_intervalo:
                return
            self._ultimo_salvar = agora
            dados = {'id': self.id, 'criado_em': self.criado_em, 'filtros': self.filtros,
                     'config': dict(CONFIG), 'ordem': self.ordem, 'chamadas': self.chamadas,
                     'rodando': self.rodando, 'atividades': self.estado}
            tmp = self._caminho_estado() + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(dados, f, ensure_ascii=False)
            os.replace(tmp, self._caminho_estado())

    def _arquivo_raw(self, aid, prefixo='raw'):
        h = hashlib.sha256(str(aid).encode()).hexdigest()[:24]
        return os.path.join(self.dir, prefixo, f'{h}.json')

    # execucao
    def iniciar(self, sincrono=False):
        """Chamado depois de reservar o job (rodando=True, sob _REG_LOCK em novo_job)."""
        with self._lock:
            self._salvar(forcar=True)
        if sincrono:
            self.executar()
        else:
            threading.Thread(target=self.executar, daemon=True).start()

    def executar(self):
        try:
            pendentes = [a for a in self.ordem
                         if self.estado[a]['status'] in ESTADOS_PENDENTES + ESTADOS_FALHA]
            with ThreadPoolExecutor(max_workers=CONFIG['concorrencia']) as ex:
                for i in range(0, len(pendentes), CONFIG['lote']):
                    futuros = [ex.submit(self._uma, a) for a in pendentes[i:i + CONFIG['lote']]]
                    for f in futuros:
                        f.result()
        finally:
            with self._lock:
                self.rodando = False
                self._salvar(forcar=True)

    def _marcar(self, aid, **campos):
        with self._lock:
            self.estado[aid].update(campos)
            self._salvar()

    def _uma(self, aid):
        destino = self._arquivo_raw(aid)
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        motivo, http = None, None
        for tentativa in range(1, CONFIG['tentativas'] + 1):
            with self._sem:
                self.limitador.aguardar()
                with self._lock:
                    self.chamadas += 1
                    self.em_voo += 1
                    self.pico_em_voo = max(self.pico_em_voo, self.em_voo)
                try:
                    resp = self.buscar(aid)
                    erro_rede = None
                except Exception as e:
                    resp, erro_rede = None, e
                finally:
                    with self._lock:
                        self.em_voo -= 1
            self._marcar(aid, tentativas=tentativa)
            if erro_rede is not None:
                motivo = 'timeout' if 'Timeout' in type(erro_rede).__name__ else 'erro_conexao'
                http = None
                self._espera(min(60, 2 ** tentativa))
                continue
            http = resp.get('status')
            if http == 200:
                lista = _lista(resp.get('corpo'))
                if lista is None:
                    return self._falha(aid, 'erro_formato', 'resposta sem lista de streams', http)
                with open(destino, 'w', encoding='utf-8') as f:
                    json.dump(resp['corpo'], f, ensure_ascii=False)
                if not lista:
                    return self._marcar(aid, status='concluida_sem_streams', origem='api',
                                        arquivo=destino, motivo=None, http=http)
                return self._marcar(aid, status='concluida', origem='api', arquivo=destino,
                                    motivo=None, http=http)
            if http == 429:
                motivo = 'HTTP 429'
                self._espera(_espera_retry_after(resp.get('retry_after')))
                continue
            if http is not None and http >= 500:
                motivo = f'HTTP {http}'
                self._espera(min(60, 2 ** tentativa))
                continue
            return self._falha(aid, 'erro_api', f'HTTP {http}', http)
        return self._falha(aid, 'erro_api', motivo or 'desconhecido', http)

    def _falha(self, aid, status, motivo, http):
        """Registra a falha. Se o banco local tiver streams, usa-os marcados como reduzidos
        e o pacote deixa de ser declarado completo."""
        loc, streams_loc = None, 0
        if status == 'erro_api':
            try:
                loc = self.local(aid)
            except Exception:
                loc = None
        if loc:
            lista = loc
            streams_loc = len(loc)
            destino = self._arquivo_raw(aid, 'local')
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            with open(destino, 'w', encoding='utf-8') as f:
                json.dump(lista, f, ensure_ascii=False)
            return self._marcar(aid, status=status, motivo=motivo, http=http,
                                origem='local_reduzido', arquivo=destino,
                                fallback_local=True, streams_locais=streams_loc)
        return self._marcar(aid, status=status, motivo=motivo, http=http, origem=None,
                            arquivo=None, fallback_local=False, streams_locais=0)

    def reiniciar_falhas(self):
        with self._lock:
            for aid, e in self.estado.items():
                if e['status'] in ESTADOS_FALHA or e['status'] == 'nao_consultada':
                    e.update(status='pendente', motivo=None)
            pacote = os.path.join(self.dir, 'pacote.zip')
            if os.path.exists(pacote):
                os.remove(pacote)
            self._salvar(forcar=True)

    def resumo(self):
        with self._lock:
            c = {'selecionadas': len(self.ordem), 'concluidas': 0, 'sem_streams': 0,
                 'erros': 0, 'pendentes': 0, 'com_fallback_local': 0}
            for e in self.estado.values():
                s = e['status']
                if s in ('concluida', 'concluida_sem_streams'):
                    c['concluidas'] += 1           # obtida com sucesso da API
                    if s == 'concluida_sem_streams':
                        c['sem_streams'] += 1      # a API respondeu lista vazia
                elif s in ESTADOS_FALHA:
                    c['erros'] += 1
                else:
                    c['pendentes'] += 1
                if e.get('fallback_local'):
                    c['com_fallback_local'] += 1
            c.update(rodando=self.rodando, chamadas_api=self.chamadas,
                     pico_pedidos_simultaneos=self.pico_em_voo,
                     completo_original=(c['erros'] == 0 and c['pendentes'] == 0
                                        and c['com_fallback_local'] == 0))
            return c

    def falhas(self):
        return [{'activity_id': a, 'motivo': e['motivo'], 'http': e['http'],
                 'tentativas': e['tentativas']}
                for a, e in self.estado.items() if e['status'] in ESTADOS_FALHA]


def _local_padrao(aid):
    """Streams guardados localmente (reduzidos). So usados se a API falhar."""
    import db
    streams, meta = db.get_streams(str(aid))
    if not streams:
        return None
    por = {m.get('key'): m for m in (meta or [])}
    lista = []
    for chave, arr in streams.items():
        m = por.get(chave, {})
        lista.append({'type': m.get('type') or chave, 'name': m.get('sensor_name'),
                      'custom': m.get('custom'), 'points': m.get('points'), 'data': arr})
    return lista


# ── registro de jobs e disco ──────────────────────────────────────────────

_REGISTRO = {}
_REG_LOCK = threading.Lock()


def _ativo():
    with _REG_LOCK:
        for j in _REGISTRO.values():
            if j.rodando:
                return j
    return None


def limpar_antigos(horas=None, agora=None, base=None):
    """Remove pastas de jobs mais antigas que o prazo e que nao estao rodando."""
    horas = CONFIG['ttl_horas'] if horas is None else horas
    base = base or DIR_BASE
    if not os.path.isdir(base):
        return 0
    limite = (agora or datetime.now()) - timedelta(hours=horas)
    removidos = 0
    for nome in os.listdir(base):
        if not _ID_JOB.match(nome):
            continue
        caminho = os.path.join(base, nome)
        with _REG_LOCK:
            if any(j.id == nome and j.rodando for j in _REGISTRO.values()):
                continue
        if datetime.fromtimestamp(os.path.getmtime(caminho)) < limite:
            shutil.rmtree(caminho, ignore_errors=True)
            removidos += 1
    return removidos


def novo_job(acts, ids, filtros, sincrono=False, buscar=None, espera=None, relogio=None,
             local=None, base=None):
    """Cria o job, grava a selecao em disco e inicia. Lanca JobEmAndamento se ja houver um."""
    base = base or DIR_BASE
    os.makedirs(base, exist_ok=True)
    limpar_antigos(base=base)
    job_id = secrets.token_hex(12)
    dir_ = os.path.join(base, job_id)
    os.makedirs(dir_)
    unicos = list(dict.fromkeys(str(i) for i in ids))
    por_id = {str(a.get('id')): a for a in acts if a.get('id') is not None}
    selec = [por_id[i] for i in unicos if i in por_id]
    with open(os.path.join(dir_, 'atividades_selecionadas.json'), 'w', encoding='utf-8') as f:
        json.dump(selec, f, ensure_ascii=False)
    job = Job(job_id, dir_, unicos, filtros, buscar=buscar, espera=espera, relogio=relogio,
              local=local)
    job.acts = {str(a['id']): a for a in selec}
    with _REG_LOCK:
        if any(j.rodando for j in _REGISTRO.values()):
            raise JobEmAndamento(next(j.id for j in _REGISTRO.values() if j.rodando))
        job.rodando = True            # reserva: nenhum outro job inicia ate este terminar
        _REGISTRO[job_id] = job
    job._salvar(forcar=True)
    job.iniciar(sincrono=sincrono)
    return job


def retomar_job(job, sincrono=False):
    """Refaz so falhas e nao consultadas. Reserva o job sob o mesmo lock de novo_job."""
    with _REG_LOCK:
        if any(j.rodando for j in _REGISTRO.values()):
            raise JobEmAndamento(next(j.id for j in _REGISTRO.values() if j.rodando))
        job.rodando = True
    job.reiniciar_falhas()
    job.iniciar(sincrono=sincrono)
    return job


def obter_job(job_id, base=None):
    if not isinstance(job_id, str) or not _ID_JOB.match(job_id):
        raise JobInexistente(job_id)
    with _REG_LOCK:
        if job_id in _REGISTRO:
            return _REGISTRO[job_id]
    base = base or DIR_BASE
    dir_ = os.path.join(base, job_id)
    cam = os.path.join(dir_, 'estado.json')
    if not os.path.isfile(cam):
        raise JobInexistente(job_id)
    with open(cam, encoding='utf-8') as f:
        dados = json.load(f)
    with open(os.path.join(dir_, 'atividades_selecionadas.json'), encoding='utf-8') as f:
        selec = json.load(f)
    job = Job(job_id, dir_, dados['ordem'], dados.get('filtros') or {})
    job.estado = dados['atividades']
    job.chamadas = dados.get('chamadas', 0)
    job.criado_em = dados.get('criado_em', job.criado_em)
    job.acts = {str(a['id']): a for a in selec}
    with _REG_LOCK:
        _REGISTRO[job_id] = job
    return job


# ── montagem do pacote (arquivos temporarios, um de cada vez) ─────────────

def _hash_arquivo(caminho):
    h = hashlib.sha256()
    with open(caminho, 'rb') as f:
        for bloco in iter(lambda: f.read(1 << 20), b''):
            h.update(bloco)
    return h.hexdigest()


def montar_pacote(job):
    """Gera pacote.zip. Lanca erro se o job ainda estiver rodando."""
    if job.rodando:
        raise JobEmAndamento(job.id)
    d = job.dir
    ids = job.ordem
    # atividades.csv e atividades.jsonl (originais, sem campos derivados)
    sel_ids = [i for i in ids if i in job.acts]
    csv_bytes, info = export.atividades_csv([job.acts[i] for i in sel_ids], sel_ids)
    p_csv = os.path.join(d, 'tmp_atividades.csv')
    with open(p_csv, 'wb') as f:
        f.write(csv_bytes)
    p_jsonl = os.path.join(d, 'tmp_atividades.jsonl')
    with open(p_jsonl, 'w', encoding='utf-8', newline='') as f:
        for i in sel_ids:
            f.write(json.dumps(export.objeto_original(job.acts[i]), ensure_ascii=False,
                               separators=(',', ':')) + '\n')

    p_streams = os.path.join(d, 'tmp_streams.csv')
    p_meta = os.path.join(d, 'tmp_streams_metadados.json')
    p_est = os.path.join(d, 'tmp_streams_estruturados.json')
    resumo_por_ativ = {}
    cobertura = {}
    chaves_obs = set()
    with open(p_streams, 'w', encoding='utf-8', newline='') as fs, \
         open(p_meta, 'w', encoding='utf-8') as fm, \
         open(p_est, 'w', encoding='utf-8') as fe:
        fs.write('﻿')
        w = csv.DictWriter(fs, fieldnames=COLUNAS_STREAMS, extrasaction='ignore',
                           lineterminator='\r\n')
        w.writeheader()
        fm.write('[')
        fe.write('[')
        primeiro_m = primeiro_e = True
        for aid in ids:
            e = job.estado.get(aid, {})
            if not e.get('arquivo') or not os.path.exists(e['arquivo']):
                resumo_por_ativ[aid] = {'status': e.get('status', 'nao_consultada')}
                continue
            with open(e['arquivo'], encoding='utf-8') as f:
                bruto = json.load(f)
            lista = _lista(bruto) if e['origem'] == 'api' else bruto
            origem = 'original_api' if e['origem'] == 'api' else 'local_reduzido'
            linhas, metas, ests, res = _processar_resposta(aid, lista or [], origem)
            for linha in linhas():
                w.writerow(linha)
                t = linha['stream_type']
                c = cobertura.setdefault(t, {'atividades': set(), 'amostras': 0, 'origem': origem})
                c['atividades'].add(aid)
                c['amostras'] += 1
            for m in metas:
                fm.write(('' if primeiro_m else ',') + json.dumps(m, ensure_ascii=False))
                primeiro_m = False
            for x in ests:
                fe.write(('' if primeiro_e else ',') + json.dumps(x, ensure_ascii=False))
                primeiro_e = False
            chaves_obs.update(res['chaves_observadas'])
            resumo_por_ativ[aid] = {'status': e['status'], 'origem': origem,
                                    'streams': res['streams'],
                                    'tipos_numericos': res['tipos_numericos'],
                                    'tipos_estruturados': res['tipos_estruturados'],
                                    'amostras': res['amostras']}
        fm.write(']\n')
        fe.write(']\n')
    cobertura = {k: {'atividades': len(v['atividades']), 'amostras': v['amostras'],
                     'origem': v['origem']} for k, v in sorted(cobertura.items())}

    arquivos = [('atividades.csv', p_csv), ('atividades.jsonl', p_jsonl),
                ('streams.csv', p_streams), ('streams_metadados.json', p_meta),
                ('streams_estruturados.json', p_est)]
    info_arq = [{'nome': n, 'bytes': os.path.getsize(p), 'sha256': _hash_arquivo(p)}
                for n, p in arquivos]
    resumo = job.resumo()
    falhas = job.falhas()
    limitacoes = [
        'Streams com origem original_api vem da resposta bruta de /activity/{id}/streams, '
        'sem downsample e sem parse_streams.',
        'Streams com origem local_reduzido vieram do banco local (reduzidos a 1500 pontos, '
        'sem tempo e sem unidades). Usados so quando a API falhou para a atividade.',
        'Atividades com erro_api ou erro_formato nao tem streams originais no pacote; '
        'ver falhas[].',
        'Atividades nao consultadas (job interrompido) estao em por_atividade com status nao_consultada.',
        'time_seconds so e preenchido quando o stream time tem o mesmo comprimento do stream '
        'da linha; caso contrario fica vazio.',
        'Unidade so e exportada quando a resposta da API a traz (campo unit).',
        'CPR excluido por politica do projeto.',
        'Campos _kj e _type_norm (derivados pelo app) removidos do JSON original.',
    ]
    manifest = {
        'versao_formato': '2.0',
        'gerado_em': datetime.now().isoformat(timespec='seconds'),
        'job_id': job.id,
        'filtros': job.filtros,
        'configuracao': dict(CONFIG),
        'atividades': resumo,
        'falhas': falhas,
        'por_atividade': [{'activity_id': a, **resumo_por_ativ.get(a, {'status': 'nao_consultada'})}
                          for a in ids],
        'streams': {'cobertura_por_tipo': cobertura,
                    'chaves_observadas': sorted(chaves_obs)},
        'pacote_completo_original': resumo['completo_original'],
        'chamadas_api_nesta_exportacao': job.chamadas,
        'sem_chamadas_externas': False,
        'limitacoes': limitacoes,
        'arquivos': info_arq,
        'nota_integridade': 'manifest.json nao se inclui no proprio hash',
    }
    p_man = os.path.join(d, 'tmp_manifest.json')
    with open(p_man, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    alvo = os.path.join(d, 'pacote.zip')
    tmp_zip = alvo + '.tmp'
    with zipfile.ZipFile(tmp_zip, 'w', zipfile.ZIP_DEFLATED) as z:
        for n, p in arquivos:
            z.write(p, arcname=n)
        z.write(p_man, arcname='manifest.json')
    os.replace(tmp_zip, alvo)
    for _, p in arquivos + [('', p_man)]:
        if os.path.exists(p):
            os.remove(p)
    return alvo, manifest
