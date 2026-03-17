import os
import re

files_to_fix = [
    "app/templates/tenant/groups/list.html",
    "app/templates/tenant/groups/add.html",
    "app/templates/tenant/groups/edit.html"
]

def add_dark_prefix(match):
    cls = match.group(1)
    
    mapping = {
        "text-white": "text-slate-900 dark:text-white",
        "text-slate-300": "text-slate-700 dark:text-slate-300",
        "text-slate-400": "text-slate-600 dark:text-slate-400",
        "text-slate-500": "text-slate-500 dark:text-slate-500",
        "bg-slate-800": "bg-white dark:bg-slate-800",
        "border-slate-700": "border-slate-300 dark:border-slate-700",
        "hover:border-slate-600": "hover:border-slate-400 dark:hover:border-slate-600",
        "hover:bg-slate-700": "hover:bg-slate-100 dark:hover:bg-slate-700",
        "bg-slate-700": "bg-slate-200 dark:bg-slate-700",
        "bg-slate-900/50": "bg-slate-100 dark:bg-slate-900/50",
        "border-slate-600": "border-slate-300 dark:border-slate-600",
    }
    
    words = cls.split()
    new_words = []
    
    for w in words:
        if w in mapping:
            # avoid appending if dark version is already there
            if not any(x.startswith("dark:") for x in words if x.endswith(w.split("-")[-1])):
                new_words.extend(mapping[w].split())
            else:
                new_words.append(w)
        else:
            new_words.append(w)
            
    # Remove duplicates preserving order
    unique_words = []
    for w in new_words:
        if w not in unique_words:
            unique_words.append(w)
            
    return 'class="' + " ".join(unique_words) + '"'

for filepath in files_to_fix:
    if not os.path.exists(filepath):
        continue
        
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # CSS Variables Fixes
    css_old = """    body {
        background-color: #0f172a !important;
        color: #e2e8f0;
    }"""
    css_new = """    body {
        background-color: #f8fafc !important;
        color: #0f172a;
    }
    .dark body {
        background-color: #0f172a !important;
        color: #e2e8f0;
    }"""
    content = content.replace(css_old, css_new)

    content = content.replace(
        "border-bottom: 1px solid #1e293b;", 
        "border-bottom: 1px solid #e2e8f0; /* light */ } .dark .device-table th, .dark .device-table td { border-bottom: 1px solid #1e293b;"
    )
    content = content.replace(
        "background: rgba(59, 130, 246, 0.05);",
        "background: rgba(59, 130, 246, 0.05); } .dark .device-table tr:hover td { background: rgba(59, 130, 246, 0.1);"
    )

    card_old = """    .card-panel {
        background: rgba(15, 23, 42, 0.8);
        border: 1px solid rgba(255, 255, 255, 0.08);"""
    card_new = """    .card-panel {
        background: rgba(255, 255, 255, 0.8);
        border: 1px solid rgba(0, 0, 0, 0.08);
    }
    .dark .card-panel {
        background: rgba(15, 23, 42, 0.8);
        border: 1px solid rgba(255, 255, 255, 0.08);"""
    content = content.replace(card_old, card_new)

    content = re.sub(r'class=[\"\']([^\"\']+)[\"\']', add_dark_prefix, content)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
        
print("Groups templates updated successfully.")
