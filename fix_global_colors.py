import os
import re

TEMPLATE_DIR = '/srv/backup_center_new/app/templates/'

replacements = {
    # Tailwind classes
    r'\bbg-slate-900\b': 'bg-[var(--card-bg)]',
    r'\bbg-slate-950\b': 'bg-[var(--bg-app)]',
    r'\bbg-slate-800\b': 'bg-[var(--card-bg)]',
    r'\bborder-slate-800\b': 'border-[var(--card-border)]',
    r'\bborder-slate-700\b': 'border-[var(--card-border)]',
    r'\btext-slate-200\b': 'text-[var(--text-main)]',
    r'\btext-slate-300\b': 'text-[var(--text-main)]',
    r'\btext-slate-400\b': 'text-[var(--text-muted)]',
    r'\btext-slate-500\b': 'text-[var(--text-muted)]',
    
    # CSS Hex Colors (used in inline styles and <style> blocks)
    r'#111113': 'var(--card-bg)',
    r'#1f2937': 'var(--card-border)',
    r'#0a0a0a': 'var(--bg-app)',
    r'#050505': 'var(--bg-app)',
    r'#0f172a': 'var(--card-bg)',
    r'#1e293b': 'var(--card-bg)',
    r'#0a0f18': 'var(--card-bg)',
    r'#0a0a0b': 'var(--bg-app)',
    r'#161618': 'var(--card-bg)',
    r'#cbd5e1': 'var(--text-main)',
    r'#f8fafc': 'var(--text-main)',
    r'#e2e8f0': 'var(--text-main)',
    r'#94a3b8': 'var(--text-muted)',
    r'#64748b': 'var(--text-muted)',
    r'#334155': 'var(--card-border)',
    r'#475569': 'var(--card-border)',
    
    # CSS rgba colors
    r'rgba\(2,\s*6,\s*23,\s*0\.34\)': 'var(--card-shadow)',
    r'rgba\(2,\s*6,\s*23,\s*0\.3\)': 'var(--card-shadow)',
    r'rgba\(2,\s*6,\s*23,\s*0\.25\)': 'var(--card-shadow)',
    r'rgba\(2,\s*6,\s*23,\s*0\.27\)': 'var(--card-shadow)',
    r'rgba\(30,\s*41,\s*59,\s*0\.55\)': 'var(--line-color)',
    r'rgba\(30,\s*41,\s*59,\s*0\.34\)': 'var(--bg-app)'
}

modified_files = 0

# Note: We skip the superadmin dashboard because it might use different variable setups or we should just be careful.
# But actually, the CSS variables are global in base.html. So it's safe.
# We also skip dashboard.html as we already modified it heavily, but applying it again won't break since the hexes are already replaced.

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
                
            if content != original_content:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(content)
                modified_files += 1
                print(f"Modified {filepath}")

print(f"Total files modified: {modified_files}")
