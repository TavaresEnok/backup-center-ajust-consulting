import os
import re

dashboard_path = "app/templates/tenant/dashboard.html"

with open(dashboard_path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Remove old CSS from dashboard.html
css_to_remove = """    /* ------------------------------------------------------------------
       THEME & VARIABLES
       ------------------------------------------------------------------ */
    :root {
        --bg-deep: #f8fafc;
        --glass-surface: rgba(255, 255, 255, 0.85);
        --glass-border: rgba(0, 0, 0, 0.08);
        --glass-highlight: rgba(0, 0, 0, 0.03);
    }
    .dark {
        --bg-deep: #02040a;
        --glass-surface: rgba(13, 18, 30, 0.65);
        --glass-border: rgba(255, 255, 255, 0.08);
        --glass-highlight: rgba(255, 255, 255, 0.03);

        --primary: #3b82f6;
        --success: #10b981;
        --warning: #f59e0b;
        --danger: #ef4444;
        --info: #06b6d4;
        --purple: #8b5cf6;
    }

    body {
        background-color: var(--bg-deep) !important;
        font-family: 'Plus Jakarta Sans', sans-serif !important;
        color: #0f172a;
        overflow-x: hidden;
    }
    .dark body {
        color: #e2e8f0;
    }"""
content = content.replace(css_to_remove, "")

# 2. Update tilt-card CSS
tilt_card_old = """    .tilt-card {
        background: var(--glass-surface);
        backdrop-filter: blur(24px) saturate(180%);
        -webkit-backdrop-filter: blur(24px) saturate(180%);
        border: 1px solid var(--glass-border);
        border-radius: 20px;
        box-shadow: 0 8px 32px -8px rgba(0, 0, 0, 0.5);
        transition: transform 0.1s ease, box-shadow 0.3s ease;
        transform-style: preserve-3d;
        position: relative;
        overflow: hidden;
    }"""
tilt_card_new = """    .tilt-card {
        transition: transform 0.1s ease;
        transform-style: preserve-3d;
        position: relative;
        overflow: hidden;
    }"""
content = content.replace(tilt_card_old, tilt_card_new)

# 3. Add premium-card class to all tilt-cards
content = content.replace('class="tilt-card', 'class="premium-card tilt-card')
content = content.replace(' tilt-card"', ' premium-card tilt-card"')

# 4. Fix specific text colors
content = content.replace('text-slate-900 dark:text-white', 'text-[var(--text-main)]')
content = content.replace('text-slate-600 dark:text-slate-400', 'text-[var(--text-muted)]')
content = content.replace('text-slate-500 dark:text-slate-500', 'text-[var(--text-muted)]')
content = content.replace('border-slate-200 dark:border-white/5', 'border-[var(--line-color)]')
content = content.replace('bg-slate-100 dark:bg-white/5', 'bg-[var(--line-color)]')
content = content.replace('bg-slate-200 dark:bg-white/10', 'bg-[var(--line-color)]')

# 5. Fix canvas particles for dark/light properly
canvas_old = """ctx.fillStyle = document.documentElement.classList.contains('dark') ? 'rgba(56, 189, 248, 0.4)' : 'rgba(59, 130, 246, 0.3)'; // Light blue"""
canvas_new = """ctx.fillStyle = document.documentElement.classList.contains('dark') ? 'rgba(56, 189, 248, 0.2)' : 'rgba(59, 130, 246, 0.15)';"""
content = content.replace(canvas_old, canvas_new)

with open(dashboard_path, "w", encoding="utf-8") as f:
    f.write(content)

print("Dashboard updated!")
