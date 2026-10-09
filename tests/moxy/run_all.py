"""Executa todos os testes de navegador da aba MOXY com fixtures sintéticos (Bike, Row, Ski).

Uso (a partir da raiz do repositório):
    python tests/moxy/run_all.py

Pré-requisitos: requirements-dev.txt instalado e um Chromium disponível para o Playwright.
Os fixtures são sintéticos; estes testes NÃO validam dados de produção.
Saída: código 0 somente se todos os suites passarem.
"""
import os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
SUITES = [
    'browser_correcao_test.py',
    'browser_principal_test.py',
    'browser_fisio_test.py',
    'browser_rz_mensagens_test.py',
    'browser_controles_test.py',
    'browser_layout_test.py',
    'browser_desenho_test.py',
]
subprocess.run([sys.executable, os.path.join(HERE, 'regen_page.py')], check=True)
falhou = False
for nome in SUITES:
    r = subprocess.run([sys.executable, os.path.join(HERE, nome)], capture_output=True, text=True, timeout=600)
    saida = r.stdout + r.stderr
    resumo = [l for l in saida.splitlines() if l.startswith('RESULTADO')]
    ruim = r.returncode != 0 or 'FALHA' in saida or 'Traceback' in saida
    falhou = falhou or ruim
    print(('ERRO ' if ruim else 'OK   ') + nome + ' | ' + (resumo[-1] if resumo else 'sem linha de resultado'))
    if ruim:
        for l in saida.splitlines():
            if 'FALHA' in l or 'Traceback' in l:
                print('     ', l[:200])
sys.exit(1 if falhou else 0)
