import os

nav_path = "app/templates/partials/navbar.html"

with open(nav_path, "r", encoding="utf-8") as f:
    content = f.read()

# Header background and border
content = content.replace(
    'bg-white border-b border-gray-200 dark:border-gray-800 dark:bg-gray-900',
    'bg-[var(--bg-app)] border-b border-[var(--line-color)]'
)

content = content.replace(
    'border-b border-gray-200 dark:border-gray-800 sm:gap-4 lg:justify-normal lg:border-b-0',
    'border-b border-[var(--line-color)] sm:gap-4 lg:justify-normal lg:border-b-0'
)

# Buttons and input in navbar
content = content.replace(
    'text-gray-500 border border-gray-200 rounded-lg hover:bg-gray-100 dark:border-gray-800 dark:text-gray-400 dark:hover:bg-gray-800',
    'text-[var(--text-muted)] border border-[var(--line-color)] rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors'
)

# Search Input
old_search = r'h-11 w-full rounded-xl border border-gray-200 bg-gray-50 pl-11 pr-4 text-sm text-gray-900 placeholder-gray-500 transition-all duration-200 focus:border-brand-500 focus:bg-white focus:outline-none focus:ring-2 focus:ring-brand-500/20 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100 dark:placeholder-gray-400 dark:focus:border-brand-400 dark:focus:bg-gray-750'
content = content.replace(old_search, 'input-premium h-11 pl-11')

# Toggler buttons (Sun/Moon/Bell)
content = content.replace(
    'bg-gray-100 dark:bg-gray-800 dark:text-gray-400 dark:hover:text-white',
    'bg-[var(--line-color)] text-[var(--text-muted)] hover:text-[var(--text-main)] hover:bg-[var(--card-border)]'
)

# Text definitions
content = content.replace('text-gray-900 dark:text-white', 'text-[var(--text-main)]')
content = content.replace('text-gray-500 dark:text-gray-400', 'text-[var(--text-muted)]')

# Dropdowns
content = content.replace(
    'bg-white shadow-lg dark:border-gray-800 dark:bg-gray-900',
    'bg-[var(--card-bg)] shadow-[var(--card-shadow)] border-[var(--card-border)] backdrop-blur-xl saturate-150'
)

with open(nav_path, "w", encoding="utf-8") as f:
    f.write(content)

print("Navbar UI updated!")
