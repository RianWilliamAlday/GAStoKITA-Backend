from requests import session
from sqlmodel import Session, select
from.database import User, UserRole, engine, Fuel, Pump
from services.auth import hash_password
import json, os

def seed_data():
    json_path = os.path.join(os.path.dirname(__file__), "..", "measurements.json")
    with open(json_path, "r") as f:
        dipstick_data = {int(k): int(v) for k, v in json.load(f).items()}

    with Session(engine) as session:
        if session.exec(select(Fuel)).first():
            print("DB already seeded")
            return

        fuels = [
            Fuel(name="Regular", price=55.0, actual_liters=8092.0, tank_capacity=8092.0),
            Fuel(name="Premium", price=60.0, actual_liters=6000.0, tank_capacity=6000.0),
            Fuel(name="Diesel", price=50.0, actual_liters=8092.0, tank_capacity=8092.0),
        ]
        session.add_all(fuels)
        session.commit()

        pumps = [
            Pump(name="Regular 1", fuel_type_id=1),
            Pump(name="Regular 2", fuel_type_id=1),
            Pump(name="Premium 1", fuel_type_id=2),
            Pump(name="Premium 2", fuel_type_id=2),
            Pump(name="Diesel 1", fuel_type_id=3),
            Pump(name="Diesel 2", fuel_type_id=3),
        ]
        session.add_all(pumps)
        session.commit()
        print("Seeded DB")

        admin_exists = session.exec(select(User).where(User.role == UserRole.ADMIN)).first()
        if not admin_exists:
            admin = User(
                name="Admin",
                email=None,
                password_hash=hash_password("admin123"),
                role=UserRole.ADMIN
            )
        session.add(admin)
        session.commit()
        print("Created default admin with password: admin123")

if __name__ == "__main__":
    from.database import create_db_and_tables
    create_db_and_tables()
    seed_data()