# -*- coding: utf-8 -*-
"""Прогон листа «Сеть и выручка» по порядку: факты → LFL/чек → площадь → фрагмент → проверки → таблицы README."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for s in ('network_facts.py', 'lfl_ticket.py', 'space_paths.py', 'make_fragment.py', 'checks.py'):
    print(f'\n######## {s}', flush=True)
    r = subprocess.run([sys.executable, '-B', os.path.join(HERE, s)], cwd=HERE)
    if r.returncode:
        sys.exit(f'{s}: код {r.returncode}')
print('\nOK: все части прошли, фрагмент book-draft/fragments/network.yaml обновлён')
