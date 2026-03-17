import os
import re

files_to_fix = [
    "app/templates/tenant/devices/list.html",
    "app/templates/tenant/groups/list.html",
    "app/templates/tenant/groups/add.html",
    "app/templates/tenant/groups/edit.html"
]

def process_file(filepath):
    if not os.path.exists(filepath):
        return
        
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Remove body background
    content = re.sub(r'body\s*{[^}]+}\s*\.dark\s*body\s*{[^}]+}', '', content, flags=re.MULTILINE | re.DOTALL)
    
    # 2. Replace hardcoded card-panel with CSS using vars, or just replace card-panel class with premium-card
    content = re.sub(r'\.card-panel\s*{[^}]+}\s*\.dark\s*\.card-panel\s*{[^}]+}', '', content, flags=re.MULTILINE | re.DOTALL)
    content = content.replace('class="card-panel"', 'class="premium-card p-6"')
    content = content.replace('card-panel', 'premium-card p-6') # For cases without exact quotes match
    
    # 3. Replace .device-table specific hardcoded colors
    table_css_old = r"""\.device-table\s*th\s*{[^}]+}\s*\.device-table\s*td\s*{[^}]+}\s*\.device-table\s*tr:hover\s*td\s*{[^}]+}"""
    
    table_css_new = """    .device-table {
        width: 100%;
        border-collapse: collapse;
    }

    .device-table th {
        text-align: left;
        padding: 0.75rem 1rem;
        font-size: 0.75rem;
        text-transform: uppercase;
        color: var(--text-muted);
        border-bottom: 1px solid var(--line-color);
        font-weight: 600;
        letter-spacing: 0.05em;
    }

    .device-table td {
        padding: 1rem;
        border-bottom: 1px solid var(--line-color);
    }

    .device-table tr:hover td {
        background: var(--line-color);
    }"""
    
    # Actually just replace the whole table section if possible
    # We will do simple string replacement for table colors to be safe from regex missing
    content = re.sub(r'\.device-table\s*th\s*{\s*text-align:\s*left;\s*padding:\s*0\.75rem\s*1rem;\s*font-size:\s*0\.75rem;\s*text-transform:\s*uppercase;\s*color:\s*#[0-9a-fA-F]+;\s*border-bottom:[^}]+}', 
                     ".device-table th {\n        text-align: left;\n        padding: 0.75rem 1rem;\n        font-size: 0.75rem;\n        text-transform: uppercase;\n        color: var(--text-muted);\n        border-bottom: 1px solid var(--line-color);\n    }", content)
    
    content = re.sub(r'\.device-table\s*td\s*{\s*padding:\s*1rem;\s*border-bottom:[^}]+}', 
                     ".device-table td {\n        padding: 1rem;\n        border-bottom: 1px solid var(--line-color);\n    }", content)
                     
    content = re.sub(r'\.device-table\s*tr:hover\s*td\s*{[^}]+}', 
                     ".device-table tr:hover td {\n        background: var(--line-color);\n    }", content)
                     
    # 4. Dropdown CSS
    dropdown_css_old = r'\/\*\s*Dropdown\s*Menu\s*Styles\s*\*\/.*?\.dropdown-count\s*{[^}]+}'
    dropdown_css_new = """/* Dropdown Menu Styles */
    .dropdown-menu {
        position: absolute;
        right: 0;
        margin-top: 8px;
        width: 280px;
        max-height: 400px;
        overflow-y: auto;
        z-index: 9999;
        background-color: var(--card-bg);
        border: 1px solid var(--card-border);
        border-radius: 12px;
        box-shadow: var(--card-shadow);
        backdrop-filter: blur(20px) saturate(180%);
        -webkit-backdrop-filter: blur(20px) saturate(180%);
    }

    .dropdown-item {
        display: block;
        padding: 10px 16px;
        font-size: 14px;
        color: var(--text-muted);
        background-color: transparent;
        text-decoration: none;
        transition: background-color 0.15s, color 0.15s;
    }

    .dropdown-item:first-child {
        border-bottom: 1px solid var(--line-color);
        padding: 12px 16px;
    }

    .dropdown-item:hover {
        background-color: var(--line-color);
        color: var(--text-main);
    }

    .dropdown-item.dropdown-active {
        background-color: rgba(59, 130, 246, 0.1);
        color: var(--primary);
    }

    .dropdown-count {
        float: right;
        color: var(--text-muted);
        font-size: 12px;
    }"""
    content = re.sub(dropdown_css_old, dropdown_css_new, content, flags=re.MULTILINE | re.DOTALL)
    
    # 5. Fix text colors in body
    content = content.replace('text-slate-900 dark:text-white', 'text-[var(--text-main)]')
    content = content.replace('text-slate-600 dark:text-slate-400', 'text-[var(--text-muted)]')
    content = content.replace('text-slate-500 dark:text-slate-500', 'text-[var(--text-muted)]')
    content = content.replace('text-white', 'text-[var(--text-main)]') # group title originally was text-white
    content = content.replace('text-gray-900 dark:text-white', 'text-[var(--text-main)]')
    
    # Restore text-white where needed (buttons)
    content = content.replace('btn-primary-neon text-[var(--text-main)]', 'btn-primary-neon text-white')
    content = content.replace('btn-primary text-[var(--text-main)]', 'btn-primary text-white')
    content = content.replace('stat-number text-[var(--text-main)]', 'stat-number text-[var(--text-main)]')
    
    # specific bg and borders tailwind classes
    content = content.replace('bg-white dark:bg-slate-800', 'bg-[var(--card-bg)]')
    content = content.replace('border-slate-300 dark:border-slate-700', 'border-[var(--card-border)]')
    content = content.replace('bg-slate-800 border-[var(--card-border)]', 'bg-[var(--card-bg)] border-[var(--card-border)]')
    
    # Fix groups list Group Cards
    content = content.replace('background: rgba(15, 23, 42, 0.6);\n        border: 1px solid rgba(255, 255, 255, 0.06);', 
                              'background: var(--card-bg);\n        border: 1px solid var(--card-border);\n        backdrop-filter: blur(20px); box-shadow: var(--card-shadow);')
    content = content.replace('border-t border-slate-800', 'border-t border-[var(--line-color)]')
    content = content.replace('text-slate-400 text-sm mt-1', 'text-[var(--text-muted)] text-sm mt-1')
    content = content.replace('background: rgba(0, 0, 0, 0.3);', 'background: var(--card-bg);')
    content = content.replace('border: 1px solid rgba(255, 255, 255, 0.1);', 'border: 1px solid var(--card-border);')
    content = content.replace('color: white;', 'color: var(--text-main);')
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

for filepath in files_to_fix:
    process_file(filepath)

print("Devices and Groups templates refactored!")
