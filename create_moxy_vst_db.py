#!/usr/bin/env python3
"""create_moxy_vst_db.py — cria moxy_vst_historico.db vazio com schema completo.

Uso:
    python3 create_moxy_vst_db.py [caminho_destino]

Padrão: ./moxy_vst_historico.db

Este ficheiro é o que o utilizador baixa e faz upload manual para o Google Drive
na primeira vez. A partir daí, a aplicação faz updates automáticos.

Nunca apaga um .db existente — recusa-se a sobrescrever dados.
"""

import os
import sys
import sqlite3

def criar(destino=None):
    destino = destino or os.path.join(os.path.dirname(__file__), "moxy_vst_historico.db")
    destino = os.path.abspath(destino)

    if os.path.exists(destino):
        print(f"ERRO: {destino} já existe.")
        print("Não vou sobrescrever um DB existente.")
        print("Se quiser um DB novo, apague o ficheiro manualmente primeiro.")
        sys.exit(1)

    # Importar o schema — pode estar no mesmo diretório
    sys.path.insert(0, os.path.dirname(__file__))
    from moxy_vst_schema import aplicar_schema

    conn = sqlite3.connect(destino)
    aplicar_schema(conn)
    conn.close()

    size = os.path.getsize(destino)
    print(f"[OK] {destino} criado ({size} bytes)")
    print()
    print("Próximos passos:")
    print("1. Faça upload deste ficheiro para a pasta do Google Drive")
    print(f"   (pasta GDRIVE_FOLDER_ID configurada na aplicação)")
    print("2. Reinicie a aplicação (ou chame /api/moxy/vst/db/status)")
    print("3. A partir daí, todos os dados MOXY/VST serão persistidos neste DB.")
    return destino


if __name__ == "__main__":
    destino = sys.argv[1] if len(sys.argv) > 1 else None
    criar(destino)
