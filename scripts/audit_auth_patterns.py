import os
import re

patterns = {
    'allowed_roles': re.compile(r'allowed_roles\s*='),
    'user.role': re.compile(r'\b(?:request\.)?user\.role\b'),
    'getattr_role': re.compile(r'getattr\([^)]*[\'\"]role[\'\"]'),
    'user.is_staff': re.compile(r'\b(?:request\.)?user\.is_staff\b'),
    'has_perm': re.compile(r'\bhas_perm\('),
    'todo_scoping': re.compile(r'TODO: branch-scoping deferred'),
    'create_perm': re.compile(r'[\'\"][a-zA-Z_]+\.create[\'\"]'),
}

for root in ['apps', 'fieldtrack', 'templates']:
    for dirpath, _, filenames in os.walk(root):
        if 'migrations' in dirpath:
            continue
        for fname in filenames:
            if not (fname.endswith('.py') or fname.endswith('.html')):
                continue
            fpath = os.path.join(dirpath, fname)
            with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                for idx, line in enumerate(f, 1):
                    for name, p in patterns.items():
                        if p.search(line):
                            print(f"[{name}] {fpath}:{idx}: {line.strip()[:120]}")
