from db.data import DIPSTICK_DATA

def get_closest_dipstick_reading(actual_liters: float) -> tuple[int, int]:
    if actual_liters <= 0:
        return 0, 0
    if actual_liters >= 8092:
        return 205, 8091
    closest_cm = 0
    closest_liters = 0
    min_diff = float('inf')
    for cm, liters in DIPSTICK_DATA.items():
        diff = abs(actual_liters - liters)
        if diff < min_diff:
            min_diff = diff
            closest_cm = cm
            closest_liters = liters
    return closest_cm, closest_liters