"""Gera tests/moxy/moxy_test.html a partir de tabs/tab_moxy.py (página local para os testes de navegador).

Não grava nada no banco. Ao importar tabs.tab_moxy, o módulo pode imprimir a linha
'DB: SQLite em ...' (comportamento já existente do módulo).
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)
from flask import Flask, render_template_string
from tabs import tab_moxy as m
from tabs.base import page

app = Flask(__name__)
with app.app_context():
    html = render_template_string(page('Moxy', m.SLUG, m.BODY, m.JS))
destino = os.path.join(HERE, 'moxy_test.html')
with open(destino, 'w', encoding='utf-8') as f:
    f.write(html)
print('regenerado', destino, len(html))
