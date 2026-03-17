import re

with open("app/templates/tenant/dashboard.html", "r", encoding="utf-8") as f:
    content = f.read()

# Replace CSS Variables
css_old = """    :root {
        --bg-deep: #02040a;
        --glass-surface: rgba(13, 18, 30, 0.65);
        --glass-border: rgba(255, 255, 255, 0.08);
        --glass-highlight: rgba(255, 255, 255, 0.03);"""
css_new = """    :root {
        --bg-deep: #f8fafc;
        --glass-surface: rgba(255, 255, 255, 0.85);
        --glass-border: rgba(0, 0, 0, 0.08);
        --glass-highlight: rgba(0, 0, 0, 0.03);
    }
    .dark {
        --bg-deep: #02040a;
        --glass-surface: rgba(13, 18, 30, 0.65);
        --glass-border: rgba(255, 255, 255, 0.08);
        --glass-highlight: rgba(255, 255, 255, 0.03);"""
        
content = content.replace(css_old, css_new)

body_old = """    body {
        background-color: var(--bg-deep) !important;
        font-family: 'Plus Jakarta Sans', sans-serif !important;
        color: #e2e8f0;
        overflow-x: hidden;
    }"""
body_new = """    body {
        background-color: var(--bg-deep) !important;
        font-family: 'Plus Jakarta Sans', sans-serif !important;
        color: #0f172a;
        overflow-x: hidden;
    }
    .dark body {
        color: #e2e8f0;
    }"""

content = content.replace(body_old, body_new)

# Fix background gradients and masks
content = content.replace("background: linear-gradient(125deg, rgba(255, 255, 255, 0.05) 0%, transparent 40%, transparent 60%, rgba(255, 255, 255, 0.02) 100%);", "background: var(--glass-highlight);")

def add_dark_prefix(match):
    cls = match.group(1)
    
    mapping = {
        "text-white": "text-slate-900 dark:text-white",
        "border-white/5": "border-slate-200 dark:border-white/5",
        "bg-white/5": "bg-slate-100 dark:bg-white/5",
        "bg-white/10": "bg-slate-200 dark:bg-white/10",
        "text-slate-400": "text-slate-600 dark:text-slate-400",
        "text-slate-500": "text-slate-500 dark:text-slate-500",
        "text-slate-600": "text-slate-700 dark:text-slate-600",
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

content = re.sub(r'class=[\"\']([^\"\']+)[\"\']', add_dark_prefix, content)

# Custom JS Fixes for Canvas and Chart text
content = content.replace(
    "ctx.fillStyle = 'rgba(56, 189, 248, 0.4)';", 
    "ctx.fillStyle = document.documentElement.classList.contains('dark') ? 'rgba(56, 189, 248, 0.4)' : 'rgba(59, 130, 246, 0.3)';"
)
content = content.replace(
    "ctx.strokeStyle = `rgba(56, 189, 248, ${0.1 * (1 - dist / 150)})`;", 
    "ctx.strokeStyle = document.documentElement.classList.contains('dark') ? `rgba(56, 189, 248, ${0.1 * (1 - dist / 150)})` : `rgba(59, 130, 246, ${0.15 * (1 - dist / 150)})`;"
)

with open("app/templates/tenant/dashboard.html", "w", encoding="utf-8") as f:
    f.write(content)
print("Dashboard updated successfully.")
