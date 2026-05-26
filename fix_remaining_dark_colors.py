import os
import re

TEMPLATE_DIR = '/srv/backup_center_new/app/templates/'

replacements = {
    # Remaining CSS Hex Colors
    r'#020617': 'var(--bg-app)',
    
    # Remaining CSS rgba backgrounds
    r'rgba\(15,\s*23,\s*42,\s*\.5\)': 'var(--card-bg)',
    r'rgba\(15,\s*23,\s*42,\s*\.55\)': 'var(--card-bg)',
    r'rgba\(15,\s*23,\s*42,\s*\.65\)': 'var(--card-bg)',
    r'rgba\(15,\s*23,\s*42,\s*\.8\)': 'var(--card-bg)',
    r'rgba\(2,\s*6,\s*23,\s*\.45\)': 'var(--bg-app)',
    r'rgba\(2,\s*6,\s*23,\s*\.48\)': 'var(--bg-app)',
    r'rgba\(2,\s*6,\s*23,\s*\.38\)': 'var(--bg-app)',
    r'rgba\(30,\s*41,\s*59,\s*\.45\)': 'var(--bg-app)',
    r'rgba\(51,\s*65,\s*85,\s*0\.55\)': 'var(--card-border)'
}

modified_files = 0

for root, dirs, files in os.walk(TEMPLATE_DIR):
    for file in files:
        if file.endswith('.html'):
            filepath = os.path.join(root, file)
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            original_content = content
            
            # Apply regex replacements
            for pattern, replacement in replacements.items():
                content = re.sub(pattern, replacement, content)
                
            # Specific replacement for backup center logo text color in sidebar
            if 'sidebar.html' in file or 'base.html' in file:
                content = re.sub(r'\.bc-brand-title\s*{\s*color:\s*#ffffff;', r'.bc-brand-title {\n            color: var(--text-main);', content)
                
            if content != original_content:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(content)
                modified_files += 1
                print(f"Modified {filepath}")

print(f"Total files modified: {modified_files}")
