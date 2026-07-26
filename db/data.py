import json
import os

json_path = os.path.join(os.path.dirname(__file__), "..", "measurements.json")
with open(json_path, "r") as f:
    DIPSTICK_DATA = {int(k): int(v) for k, v in json.load(f).items()}

TANK_CAPACITY = 8092.0

fuel_types = [
    {"id": 1, "name": "Regular", "price": 55.0, "actual_liters": 8092.0},
    {"id": 2, "name": "Premium", "price": 60.0, "actual_liters": 8092.0},
    {"id": 3, "name": "Diesel", "price": 50.0, "actual_liters": 8092.0},
]

pumps = [
    {"id": 1, "name": "Regular 1", "fuel_type_id": 1, "status": "Available"},
    {"id": 2, "name": "Regular 2", "fuel_type_id": 1, "status": "Available"},
    {"id": 3, "name": "Premium 1", "fuel_type_id": 2, "status": "Available"},
    {"id": 4, "name": "Premium 2", "fuel_type_id": 2, "status": "Available"},
    {"id": 5, "name": "Diesel 1", "fuel_type_id": 3, "status": "Available"},
    {"id": 6, "name": "Diesel 2", "fuel_type_id": 3, "status": "Available"},
]