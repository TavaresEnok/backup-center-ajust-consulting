from app.core.database import SessionLocal
from app.models.plan import Plan

def seed_plans():
    db = SessionLocal()
    try:
        plans_data = [
            {
                "name": "Free Tier",
                "slug": "free",
                "description": "Para pequenos provedores e testes.",
                "price_monthly": 0,
                "price_yearly": 0,
                "max_devices": 5,
                "max_users": 1,
                "features": {"backup_daily": True, "email_support": False},
                "trial_days": 0
            },
            {
                "name": "Pro",
                "slug": "pro",
                "description": "Ideal para provedores em crescimento.",
                "price_monthly": 4990, # R$ 49,90
                "price_yearly": 49900, # R$ 499,00
                "max_devices": 50,
                "max_users": 5,
                "features": {"backup_daily": True, "email_support": True, "priority_support": False},
                "trial_days": 14
            },
            {
                "name": "Enterprise",
                "slug": "enterprise",
                "description": "Para grandes operações e alta demanda.",
                "price_monthly": 14990, # R$ 149,90
                "price_yearly": 149900, # R$ 1499,00
                "max_devices": 500,
                "max_users": 20,
                "features": {"backup_daily": True, "email_support": True, "priority_support": True},
                "trial_days": 14
            }
        ]
        
        for p_data in plans_data:
            plan = db.query(Plan).filter(Plan.slug == p_data['slug']).first()
            if not plan:
                new_plan = Plan(**p_data)
                db.add(new_plan)
                print(f"Created Plan: {p_data['name']}")
            else:
                print(f"Plan exists: {p_data['name']}")
        
        db.commit()
    except Exception as e:
        print(f"Error seeding plans: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    seed_plans()
