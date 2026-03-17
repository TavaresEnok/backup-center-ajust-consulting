import os

base_path = "app/templates/base.html"
with open(base_path, "r", encoding="utf-8") as f:
    content = f.read()

# Add standard primary colors to :root
root_colors = """            --line-color: #e2e8f0;

            --primary: #3b82f6;
            --success: #10b981;
            --warning: #f59e0b;
            --danger: #ef4444;
            --info: #06b6d4;
            --purple: #8b5cf6;"""
content = content.replace("--line-color: #e2e8f0;", root_colors)

# Add standard primary colors to .dark
dark_colors = """            --line-color: rgba(255, 255, 255, 0.05);

            --primary: #3b82f6;
            --success: #10b981;
            --warning: #f59e0b;
            --danger: #ef4444;
            --info: #06b6d4;
            --purple: #8b5cf6;"""
content = content.replace("--line-color: rgba(255, 255, 255, 0.05);", dark_colors)

with open(base_path, "w", encoding="utf-8") as f:
    f.write(content)

print("Added extra tokens to base.html!")
