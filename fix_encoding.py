# -*- coding: utf-8 -*-
import os
import glob

templates_dir = r'c:\dev\backup_manager\app\templates'

# Mapeamento de caracteres corrompidos para corretos
fixes = {
    # Acentos minúsculos
    'Ã¡': 'á', 'Ã ': 'à', 'Ã¢': 'â', 'Ã£': 'ã', 'Ã¤': 'ä',
    'Ã©': 'é', 'Ã¨': 'è', 'Ãª': 'ê', 'Ã«': 'ë',
    'Ã­': 'í', 'Ã¬': 'ì', 'Ã®': 'î', 'Ã¯': 'ï',
    'Ã³': 'ó', 'Ã²': 'ò', 'Ã´': 'ô', 'Ãµ': 'õ', 'Ã¶': 'ö',
    'Ãº': 'ú', 'Ã¹': 'ù', 'Ã»': 'û', 'Ã¼': 'ü',
    'Ã§': 'ç',
    # Acentos maiúsculos
    'Ãš': 'Ú', 'Ã‡': 'Ç', 'Ã•': 'Õ',
    # Símbolos
    'Âº': 'º', 'Âª': 'ª', 'Â°': '°',
    # Bullet e traços (problema principal)
    'â€¢': '•',
    'â€"': '–',
    'â€"': '—',
    # Aspas curvas
    'â€œ': '"',
    'â€': '"',
    # Ã seguido de espaço ou fim
    'Ã\xa0': 'à',
}

count = 0
for filepath in glob.glob(os.path.join(templates_dir, '**', '*.html'), recursive=True):
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        
        original = content
        for wrong, correct in fixes.items():
            content = content.replace(wrong, correct)
        
        if content != original:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            count += 1
            print(f'Fixed: {os.path.basename(filepath)}')
    except Exception as e:
        print(f'Error in {filepath}: {e}')

print(f'\nTotal files fixed: {count}')
