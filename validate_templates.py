from jinja2 import Environment, FileSystemLoader
import sys
import os

def validate(filename):
    env = Environment(loader=FileSystemLoader('app/templates'))
    try:
        rel_path = filename.replace('app/templates/', '').replace('\\', '/')
        env.get_template(rel_path)
        print(f"OK: {rel_path}")
    except Exception as e:
        print(f"ERRO em {filename}: {e}")
        if hasattr(e, 'lineno'):
            print(f"  Linha: {e.lineno}")

if __name__ == "__main__":
    with open('validation.log', 'w') as log:
        sys.stdout = log
        try:
            validate('app/templates/tenant/backups/list.html')
            validate('app/templates/tenant/compare/result.html')
        except Exception as e:
            print(f"CRITICAL ERROR: {e}")
