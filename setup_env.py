import os

# Create essential directories
dirs = [
    "storage/backups",
    "app/static/css",
    "app/static/js",
    "app/static/images",
    "migrations",
]

for d in dirs:
    os.makedirs(d, exist_ok=True)

# Create an empty .gitkeep in storage to ensure it exists
with open("storage/.gitkeep", "w") as f:
    pass
