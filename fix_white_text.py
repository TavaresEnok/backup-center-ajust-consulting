import os
import re

files = [
    '/srv/backup_center_new/app/templates/tenant/operations/index.html',
    '/srv/backup_center_new/app/templates/tenant/devices/list.html',
    '/srv/backup_center_new/app/templates/tenant/schedules/list.html'
]

for filepath in files:
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        
    for i in range(len(lines)):
        # Check if the line has color: #fff;
        if 'color: #fff;' in lines[i]:
            # Don't replace if it's an active pagination button, primary button, or gradient
            text_context = ''.join(lines[max(0, i-2):i+2])
            
            if 'background: #4f46e5' in text_context or \
               'background: linear-gradient' in text_context or \
               '.ops-btn-indigo' in text_context or \
               '.dev-btn-indigo' in text_context or \
               '.sch-btn-primary' in text_context or \
               'page-btn.active' in text_context:
                continue
                
            lines[i] = lines[i].replace('color: #fff;', 'color: var(--text-main);')
            
    with open(filepath, 'w', encoding='utf-8') as f:
        f.writelines(lines)
        
print("Fixed white texts successfully!")
