import os
import re

files_to_update = [
    "app/templates/tenant/devices/list.html",
    "app/templates/tenant/groups/list.html",
    "app/templates/tenant/groups/add.html",
    "app/templates/tenant/groups/edit.html"
]

def refactor(path):
    if not os.path.exists(path):
        return
        
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # REMOVE CUSTOM BODY STYLES
    body_styles = re.findall(r'body\s*{[^}]+}', content)
    for b in body_styles: content = content.replace(b, "")
    dark_body_styles = re.findall(r'\.dark body\s*{[^}]+}', content)
    for b in dark_body_styles: content = content.replace(b, "")

    # REPLACE .card-panel with .premium-card (CSS and HTML)
    cpanel_css = re.findall(r'\.card-panel\s*{[^}]+}', content)
    for c in cpanel_css: content = content.replace(c, "")
    dcpanel_css = re.findall(r'\.dark \.card-panel\s*{[^}]+}', content)
    for c in dcpanel_css: content = content.replace(c, "")
    content = content.replace('class="card-panel"', 'class="premium-card p-6"')
    content = content.replace('class="card-panel ', 'class="premium-card p-6 ')

    # TABLE FIXES
    content = re.sub(r'border-bottom:\s*1px\s*solid\s*#e2e8f0;.*?\}', 'border-bottom: 1px solid var(--line-color); }', content, flags=re.DOTALL)
    content = re.sub(r'background:\s*rgba\(59,\s*130,\s*246,\s*0\.05\);.*?\}', 'background: var(--line-color); }', content, flags=re.DOTALL)
    content = content.replace('color: #64748b;', 'color: var(--text-muted);')

    # DROPDOWN
    content = re.sub(r'background-color:\s*#ffffff;', 'background-color: var(--card-bg);', content)
    content = re.sub(r'border:\s*1px\s*solid\s*#e2e8f0;', 'border: 1px solid var(--card-border);', content)
    content = re.sub(r'box-shadow:.*?rgba\(0,\s*0,\s*0,\s*0\.1[^\)]*\);', 'box-shadow: var(--card-shadow);\n        backdrop-filter: blur(20px);', content)
    content = re.sub(r'color:\s*#475569;', 'color: var(--text-muted);', content)
    content = re.sub(r'background-color:\s*#f1f5f9;', 'background-color: var(--line-color); color: var(--text-main);', content)
    content = re.sub(r'background-color:\s*#ebf8ff;', 'background-color: rgba(59, 130, 246, 0.1);', content)
    content = re.sub(r'color:\s*#2563eb;', 'color: var(--primary);', content)
    
    # Remove .dark dropdown overwrites completely
    dark_drops = re.findall(r'\.dark \.dropdown-[a-z]+\s*{[^}]+}', content)
    for d in dark_drops: content = content.replace(d, "")
    
    # Text Tailwind tokens
    content = content.replace('text-slate-900 dark:text-white', 'text-[var(--text-main)]')
    content = content.replace('text-slate-600 dark:text-slate-400', 'text-[var(--text-muted)]')
    content = content.replace('text-slate-500 dark:text-slate-500', 'text-[var(--text-muted)]')
    content = content.replace('text-gray-900 dark:text-white', 'text-[var(--text-main)]')
    content = content.replace('bg-white dark:bg-slate-800', 'bg-[var(--card-bg)]')
    content = content.replace('border-slate-300 dark:border-slate-700', 'border-[var(--card-border)]')
    content = content.replace('bg-slate-200 dark:bg-slate-700', 'bg-[var(--line-color)]')
    content = content.replace('text-slate-700 dark:text-slate-300', 'text-[var(--text-main)] font-medium')
    
    # Groups fixes
    content = content.replace('background: rgba(15, 23, 42, 0.6);', 'background: var(--card-bg);')
    content = content.replace('border: 1px solid rgba(255, 255, 255, 0.06);', 'border: 1px solid var(--card-border);\n        box-shadow: var(--card-shadow);\n        backdrop-filter: blur(20px);')
    content = content.replace('border-slate-800', '[var(--line-color)]')
    content = content.replace('background: rgba(0, 0, 0, 0.3);', 'background: var(--card-bg);')
    content = content.replace('border: 1px solid rgba(255, 255, 255, 0.1);', 'border: 1px solid var(--card-border);')
    
    # restore btn neon text to white instead of inheriting main
    content = content.replace('btn-primary-neon text-[var(--text-main)]', 'btn-primary-neon text-white')
    content = content.replace('text-[var(--text-main)] font-semibold', 'text-[var(--text-main)] font-semibold')
    
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

for f in files_to_update:
    refactor(f)
print("Updated all list files recursively")
