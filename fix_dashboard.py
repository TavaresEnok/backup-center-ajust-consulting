import re

with open('/srv/backup_center_new/app/templates/tenant/dashboard.html', 'r') as f:
    content = f.read()

# 1. CSS Replacements
replacements = {
    'background: #0a0a0a;': 'background: var(--bg-app);',
    'color: #cbd5e1;': 'color: var(--text-main);',
    'background: #111113;': 'background: var(--card-bg);',
    'border: 1px solid #1f2937;': 'border: 1px solid var(--card-border);',
    'border-bottom: 1px solid #1f2937;': 'border-bottom: 1px solid var(--card-border);',
    'box-shadow: 0 14px 30px rgba(2, 6, 23, 0.3);': 'box-shadow: var(--card-shadow);',
    'box-shadow: 0 10px 24px rgba(2, 6, 23, 0.25);': 'box-shadow: var(--card-shadow);',
    'box-shadow: 0 12px 28px rgba(2, 6, 23, 0.27);': 'box-shadow: var(--card-shadow);',
    'color: #f8fafc;': 'color: var(--text-main);',
    'color: #94a3b8;': 'color: var(--text-muted);',
    'color: #64748b;': 'color: var(--text-muted);',
    'color: #e2e8f0;': 'color: var(--text-main);',
    'border-bottom: 1px solid rgba(30, 41, 59, 0.55);': 'border-bottom: 1px solid var(--line-color);',
    'background: #0a0a0b;': 'background: var(--bg-app);',
    'background: #161618;': 'background: var(--card-bg);',
    'background: rgba(30, 41, 59, 0.34);': 'background: var(--bg-app);',
    'background: #0f172a;': 'background: var(--bg-app);',
    'border: 1px solid #334155;': 'border: 1px solid var(--card-border);',
    'border-bottom: 1px solid #1f2937;': 'border-bottom: 1px solid var(--card-border);',
    'strong style="color:#e2e8f0;"': 'strong style="color:var(--text-main);"',
}

for old, new in replacements.items():
    content = content.replace(old, new)

# 2. HTML Removals - Remove Specific Cards
cards_to_remove = [
    r'<article class="dash-metric">\s*<div class="dash-metric-head"><span class="dash-icon amber"><i data-lucide="clock"></i></span></div>\s*<div class="dash-metric-value">{{ pending_backups }}</div>\s*<div class="dash-metric-label">Tarefas Pendentes</div>.*?<\/article>',
    r'<article class="dash-metric">\s*<div class="dash-metric-head"><span class="dash-icon rose"><i data-lucide="shield-alert"></i></span></div>\s*<div class="dash-metric-value">{{ no_history_count }}</div>\s*<div class="dash-metric-label">Dispositivos Sem Backup</div>.*?<\/article>',
    r'<article class="dash-metric">\s*<div class="dash-metric-head"><span class="dash-icon sky"><i data-lucide="calendar-range"></i></span></div>\s*<div class="dash-metric-value">{{ total_scheduled }}</div>\s*<div class="dash-metric-label">Dispositivos Agendados</div>.*?<\/article>',
    r'<article class="dash-metric">\s*<div class="dash-metric-head"><span class="dash-icon indigo"><i data-lucide="check-check"></i></span></div>\s*<div class="dash-metric-value">{{ backups_today }}</div>\s*<div class="dash-metric-label">Processados Hoje</div>.*?<\/article>',
    r'<article class="dash-metric">\s*<div class="dash-metric-head"><span class="dash-icon amber"><i data-lucide="hourglass"></i></span></div>\s*<div class="dash-metric-value">{{ pending_backups }}</div>\s*<div class="dash-metric-label">Pendentes para Execucao</div>.*?<\/article>'
]

for pattern in cards_to_remove:
    content = re.sub(pattern, '', content, flags=re.DOTALL)

# Now, we have an empty "Saude Operacional" section if we move its remaining cards to "Visao por Prioridade"
# Let's extract the remaining cards in Saude Operacional and put them in Visao por Prioridade
saude_cards = re.findall(r'<article class="dash-metric">.*?<\/article>', content[content.find('<p class="dash-kicker">Saude Operacional</p>'):content.find('<section class="dash-grid-main">')], re.DOTALL)

# Append these cards to the end of Visao por Prioridade
if saude_cards:
    insert_pos = content.find('      </div>\n    </section>\n\n    <section>\n      <p class="dash-kicker">Saude Operacional</p>')
    if insert_pos != -1:
        # Move them up
        replacement = '\n'.join(saude_cards) + '\n      </div>\n    </section>'
        content = content[:insert_pos] + replacement + content[insert_pos+31:]
        
# Remove the Saude Operacional section
content = re.sub(r'<section>\s*<p class="dash-kicker">Saude Operacional</p>\s*<div class="dash-grid-5">\s*<\/div>\s*<\/section>', '', content, flags=re.DOTALL)
content = re.sub(r'<section>\s*<p class="dash-kicker">Saude Operacional</p>\s*<div class="dash-grid-5">.*?<\/div>\s*<\/section>', '', content, flags=re.DOTALL)


# 3. Add Server Metrics Card
server_metrics_html = """
    <section>
      <p class="dash-kicker">Recursos do Servidor (Tempo Real)</p>
      <div class="dash-grid-5" style="grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));">
        
        <article class="dash-metric">
          <div class="dash-metric-head">
            <span class="dash-icon sky"><i data-lucide="cpu"></i></span>
            <span class="dash-metric-label">Processador (CPU)</span>
          </div>
          <div class="dash-metric-value" id="server-cpu-val">--%</div>
          <div class="dash-meter"><span id="server-cpu-bar" style="width: 0%; background:#38bdf8; transition: width 0.5s ease;"></span></div>
        </article>

        <article class="dash-metric">
          <div class="dash-metric-head">
            <span class="dash-icon purple"><i data-lucide="memory-stick"></i></span>
            <span class="dash-metric-label">Memória (RAM)</span>
          </div>
          <div class="dash-metric-value" id="server-ram-val">--%</div>
          <div class="dash-metric-sub" id="server-ram-sub">-- GB / -- GB</div>
          <div class="dash-meter"><span id="server-ram-bar" style="width: 0%; background:#c084fc; transition: width 0.5s ease;"></span></div>
        </article>

        <article class="dash-metric">
          <div class="dash-metric-head">
            <span class="dash-icon indigo"><i data-lucide="hard-drive"></i></span>
            <span class="dash-metric-label">Disco (Sistema)</span>
          </div>
          <div class="dash-metric-value" id="server-disk-val">--%</div>
          <div class="dash-metric-sub" id="server-disk-sub">-- GB / -- GB</div>
          <div class="dash-meter"><span id="server-disk-bar" style="width: 0%; background:#6366f1; transition: width 0.5s ease;"></span></div>
        </article>

      </div>
    </section>
"""

# Insert before dash-grid-main
content = content.replace('<section class="dash-grid-main">', server_metrics_html + '\n    <section class="dash-grid-main">')

# 4. Add JS polling script for server metrics
polling_script = """
  function fetchServerMetrics() {
    fetch('{{ url_for("tenant.server_metrics", tenant_slug=tenant_slug) }}')
      .then(res => res.json())
      .then(data => {
        document.getElementById('server-cpu-val').innerText = data.cpu_percent + '%';
        document.getElementById('server-cpu-bar').style.width = data.cpu_percent + '%';
        
        document.getElementById('server-ram-val').innerText = data.ram_percent + '%';
        document.getElementById('server-ram-sub').innerText = data.ram_used_gb + ' GB / ' + data.ram_total_gb + ' GB';
        document.getElementById('server-ram-bar').style.width = data.ram_percent + '%';
        
        document.getElementById('server-disk-val').innerText = data.disk_percent + '%';
        document.getElementById('server-disk-sub').innerText = data.disk_used_gb + ' GB / ' + data.disk_total_gb + ' GB';
        document.getElementById('server-disk-bar').style.width = data.disk_percent + '%';
      })
      .catch(err => console.error("Falha ao buscar metricas:", err));
  }
  fetchServerMetrics();
  setInterval(fetchServerMetrics, 3000);
"""

content = content.replace('if (window.Chart) {', polling_script + '\n\n  if (window.Chart) {')

with open('/srv/backup_center_new/app/templates/tenant/dashboard.html', 'w') as f:
    f.write(content)
print("Dashboard UI and CSS updated successfully.")
