"""
Add Jump Host columns to device_groups table
"""

from app.core.database import engine
from sqlalchemy import text

def upgrade():
    """Add jump host columns to device_groups table."""
    
    columns_to_add = [
        ("connection_type", "VARCHAR(50) DEFAULT 'direct'"),
        ("uses_jump_host", "BOOLEAN DEFAULT FALSE"),
        ("jump_host", "VARCHAR(255)"),
        ("jump_port", "INTEGER DEFAULT 22"),
        ("jump_username", "VARCHAR(255)"),
        ("jump_password_encrypted", "TEXT"),
        ("jump_key_encrypted", "TEXT"),
    ]
    
    with engine.connect() as conn:
        for column_name, column_type in columns_to_add:
            try:
                conn.execute(text(f"""
                    ALTER TABLE device_groups 
                    ADD COLUMN IF NOT EXISTS {column_name} {column_type}
                """))
                print(f"✓ Added column: {column_name}")
            except Exception as e:
                if "already exists" in str(e).lower():
                    print(f"• Column {column_name} already exists, skipping...")
                else:
                    print(f"✗ Error adding {column_name}: {e}")
        
        conn.commit()
        print("\n✓ Migration completed successfully!")


if __name__ == "__main__":
    upgrade()
