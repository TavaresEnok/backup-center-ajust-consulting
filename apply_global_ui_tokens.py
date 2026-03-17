import os
import re

TARGET_DIR = "app/templates"

BTN_PRIMARY_REGEX = r'class="[^"]*(?:btn-primary-neon|btn\s+btn-primary|bg-brand-500\s+hover:bg-brand-600)[^"]*"'
BTN_DANGER_REGEX = r'class="[^"]*(?:btn-danger|bg-error-500\s+hover:bg-error-600)[^"]*"'
BTN_SECONDARY_REGEX = r'class="[^"]*(?:bg-gray-100\s+hover:bg-gray-200|bg-white\s+border\s+border-gray-200\s+hover:bg-gray-50)[^"]*text-gray-700[^"]*"'

INPUT_REGEX = r'class="[^"]*(?:w-full\s+rounded-lg\s+border\s+border-gray-300|focus:border-brand-500)[^"]*"'

def replace_classes(content):
    # We apply simpler regexes to avoid destroying important structural classes (like w-full, px, py), 
    # but since btn-premium handles padding and rounding, we can replace the whole class string if it's purely a button
    # Actually, the safest way is to leave spacing classes but replace colors/borders/shadows.
    
    # 1. Inputs
    # Catch big tailwind input strings
    old_input = r'w-full rounded-lg border border-gray-\d00 bg-transparent py-2\.5 pl-11 pr-\d+ text-gray-800 shadow-theme-xs placeholder:text-gray-400 focus:border-brand-500 focus:ring-brand-500/10 dark:border-gray-700 dark:bg-gray-900 dark:text-white dark:placeholder:text-gray-500 dark:focus:border-brand-500'
    content = re.sub(old_input, 'input-premium py-2.5 pl-11', content)
    
    old_input2 = r'w-full\s+rounded-(?:lg|md)\s+border\s+border-(?:gray|slate)-\d00\s+(?:bg-white|bg-transparent|bg-gray-50)\s+(?:px-\d\s+py-\d|p-\d)\s+text-(?:gray|slate)-[0-9]{3}\s+(?:shadow-sm\s+)?focus:(?:border|ring)-(?:brand|blue)-500\s+focus:(?:outline-none|ring-\d).*?(?:dark:[^\s"{}]+)*'
    content = re.sub(old_input2, 'input-premium', content)
    
    # 2. Buttons
    content = re.sub(r'class="[^"]*btn-primary-neon[^"]*"', 'class="btn-premium flex items-center gap-2"', content)
    
    # Let's search for "btn btn-primary"
    content = content.replace('btn btn-primary', 'btn-premium')
    content = content.replace('btn-primary', 'btn-premium')
    content = content.replace('btn btn-danger', 'btn-danger')
    content = content.replace('btn btn-secondary', 'btn-secondary')
    
    # Convert 'text-slate-...' and 'text-gray-...' to text-[var(--text-main/muted)] was partially done, but let's stick to the new Tailwind system (gray-900 / gray-500)
    # Actually, in ui_redesign_v2 we are standardizing on gray-900 for dark text, gray-500 for muted, out of the standard tailwind palette, which is already configured for dark mode.
    
    return content

for root, _, files in os.walk(TARGET_DIR):
    for file in files:
        if file.endswith(".html"):
            path = os.path.join(root, file)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            
            new_content = replace_classes(content)
            
            if new_content != content:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(new_content)

print("Applied UI tokens to HTML files.")
