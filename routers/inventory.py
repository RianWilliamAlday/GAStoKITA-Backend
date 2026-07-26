from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select
from db.database import get_session, Fuel, Sale, OilProduct, OilSale
from models.schemas import (
    InventoryMetrics,
    UnifiedInventoryMetrics,
    AIInventoryMetrics,
    AIUnifiedInventoryMetrics,
    UnifiedLowStockItem,
    UnifiedLowStockResponse
)
from ..services.ai_feature import get_ai_reorder_insights
from statistics import mean, stdev
from collections import defaultdict
from datetime import datetime, date, timedelta
from typing import List, Literal

router = APIRouter(prefix="/api/ai-inventory", tags=["ai-inventory"])

ProductType = Literal["fuel", "oil", "all"]

def get_fuel_inventory_optimization(session: Session) -> List[InventoryMetrics]:
    LEAD_TIME_DAYS = 3
    Z_SCORE = 1.65
    ORDERING_COST = 1500.0
    today = date.today()
    start_dt = datetime.combine(today - timedelta(days=14), datetime.min.time())
    fuels = session.exec(select(Fuel)).all()
    all_sales = session.exec(select(Sale).where(Sale.sold_at >= start_dt)).all()
    results: List[InventoryMetrics] = []
    for fuel in fuels:
        daily = defaultdict(float)
        for s in all_sales:
            if s.fuel_id == fuel.id:
                daily[s.sold_at.date()] += s.liters_sold
        daily_values = []
        for i in range(14):
            d = today - timedelta(days=13 - i)
            daily_values.append(daily.get(d, 0.0))
        avg_daily = round(mean(daily_values), 2) if daily_values else 0.0
        std_dev = round(stdev(daily_values), 2) if len(daily_values) > 1 else 0.0
        max_daily = round(max(daily_values), 2) if daily_values else 0.0
        first_week = mean(daily_values[:7]) if daily_values else 0
        last_week = mean(daily_values[7:]) if daily_values else 0
        if last_week > first_week * 1.1:
            trend = "increasing"
        elif last_week < first_week * 0.9:
            trend = "decreasing"
        else:
            trend = "stable"
        current_stock = float(fuel.actual_liters)
        days_remaining = round(current_stock / avg_daily, 1) if avg_daily > 0 else 999.0
        safety_stock = round(Z_SCORE * std_dev * (LEAD_TIME_DAYS ** 0.5), 2)
        reorder_point = round(avg_daily * LEAD_TIME_DAYS + safety_stock, 2)
        max_stock_level = round(fuel.tank_capacity * 0.9, 2)
        should_reorder = current_stock <= reorder_point
        suggested_quantity = round(max(0, max_stock_level - current_stock), 2) if should_reorder else 0.0
        annual_demand = avg_daily * 365
        holding_cost = fuel.price * 0.20
        if holding_cost > 0 and annual_demand > 0:
            eoq = (2 * annual_demand * ORDERING_COST / holding_cost) ** 0.5
            economic_order_qty = round(min(eoq, max_stock_level), 2)
        else:
            economic_order_qty = max_stock_level
        if current_stock <= fuel.threshold or days_remaining < 2:
            urgency = "critical"
        elif current_stock <= reorder_point or days_remaining < 5:
            urgency = "warning"
        elif current_stock > fuel.tank_capacity * 0.85:
            urgency = "overstocked"
        else:
            urgency = "normal"
        suggested_reorder_date = today if should_reorder else today + timedelta(days=max(0, int(days_remaining - LEAD_TIME_DAYS)))
        cost_of_order = round(suggested_quantity * fuel.price, 2)
        days_after = round((current_stock + suggested_quantity) / avg_daily, 1) if avg_daily > 0 else 999.0
        message = f"{fuel.name}: {current_stock:.0f}L left, ~{days_remaining} days at {avg_daily}L/day"
        results.append(InventoryMetrics(
            fuel_id=fuel.id, fuel_name=fuel.name, current_stock=current_stock, tank_capacity=fuel.tank_capacity,
            threshold=fuel.threshold, avg_daily_usage=avg_daily, usage_std_dev=std_dev, max_daily_usage=max_daily,
            trend=trend, days_remaining=days_remaining, safety_stock=safety_stock, reorder_point=reorder_point,
            economic_order_qty=economic_order_qty, max_stock_level=max_stock_level, should_reorder=should_reorder,
            suggested_reorder_date=suggested_reorder_date, suggested_quantity=suggested_quantity, urgency=urgency,
            message=message, cost_of_order=cost_of_order, days_of_supply_after_reorder=days_after
        ))
    return results

def get_oil_inventory_optimization(session: Session) -> List[UnifiedInventoryMetrics]:
    LEAD_TIME_DAYS = 5
    Z_SCORE = 1.65
    ORDERING_COST = 500.0
    today = date.today()
    start_dt = datetime.combine(today - timedelta(days=14), datetime.min.time())

    oils = session.exec(select(OilProduct)).all()
    all_sales = session.exec(select(OilSale).where(OilSale.sold_at >= start_dt)).all()

    results: List[UnifiedInventoryMetrics] = []
    for oil in oils:
        daily = defaultdict(float)
        for s in all_sales:
            if s.oil_product_id == oil.id:
                daily[s.sold_at.date()] += float(s.quantity)

        daily_values = []
        for i in range(14):
            d = today - timedelta(days=13 - i)
            daily_values.append(daily.get(d, 0.0))

        avg_daily = round(mean(daily_values), 2) if daily_values else 0.0
        std_dev = round(stdev(daily_values), 2) if len(daily_values) > 1 else 0.0
        max_daily = round(max(daily_values), 2) if daily_values else 0.0

        first_week = mean(daily_values[:7]) if daily_values else 0
        last_week = mean(daily_values[7:]) if daily_values else 0
        if last_week > first_week * 1.1:
            trend = "increasing"
        elif last_week < first_week * 0.9:
            trend = "decreasing"
        else:
            trend = "stable"

        current_stock = float(oil.stock)
        days_remaining = round(current_stock / avg_daily, 1) if avg_daily > 0 else 999.0

        safety_stock = round(Z_SCORE * std_dev * (LEAD_TIME_DAYS ** 0.5), 2)
        reorder_point = round(avg_daily * LEAD_TIME_DAYS + safety_stock, 2)

        max_stock_level = round(max(float(oil.low_stock_threshold) * 4, 20.0), 2)
        should_reorder = current_stock <= reorder_point

        suggested_quantity = round(max(0, max_stock_level - current_stock), 2) if should_reorder else 0.0

        annual_demand = avg_daily * 365
        holding_cost = oil.price * 0.20
        if holding_cost > 0 and annual_demand > 0:
            eoq = (2 * annual_demand * ORDERING_COST / holding_cost) ** 0.5
            economic_order_qty = round(min(eoq, max_stock_level), 2)
        else:
            economic_order_qty = max_stock_level

        if current_stock <= oil.low_stock_threshold or days_remaining < 2:
            urgency = "critical"
        elif current_stock <= reorder_point or days_remaining < 5:
            urgency = "warning"
        elif current_stock > max_stock_level * 0.9:
            urgency = "overstocked"
        else:
            urgency = "normal"

        suggested_reorder_date = today if should_reorder else today + timedelta(days=max(0, int(days_remaining - LEAD_TIME_DAYS)))
        cost_of_order = round(suggested_quantity * oil.price, 2)
        days_after = round((current_stock + suggested_quantity) / avg_daily, 1) if avg_daily > 0 else 999.0

        product_name = f"{oil.brand} {oil.name}" + (f" {oil.variant}" if oil.variant else "")

        message = f"{product_name}: {int(current_stock)} pcs left, ~{days_remaining} days at {avg_daily} pcs/day"

        results.append(UnifiedInventoryMetrics(
            product_type="oil",
            product_id=oil.id,
            product_name=product_name,
            brand=oil.brand,
            unit="pcs",
            current_stock=current_stock,
            tank_capacity=max_stock_level,
            threshold=float(oil.low_stock_threshold),
            avg_daily_usage=avg_daily,
            usage_std_dev=std_dev,
            max_daily_usage=max_daily,
            trend=trend,
            days_remaining=days_remaining,
            safety_stock=safety_stock,
            reorder_point=reorder_point,
            economic_order_qty=economic_order_qty,
            max_stock_level=max_stock_level,
            should_reorder=should_reorder,
            suggested_reorder_date=suggested_reorder_date,
            suggested_quantity=suggested_quantity,
            urgency=urgency,
            message=message,
            cost_of_order=cost_of_order,
            days_of_supply_after_reorder=days_after,
            fuel_id=None,
            fuel_name=None,
            oil_id=oil.id,
            oil_brand=oil.brand
        ))
    return results

def get_inventory_optimization(session: Session) -> List[UnifiedInventoryMetrics]:
    fuel_metrics = get_fuel_inventory_optimization(session)
    unified: List[UnifiedInventoryMetrics] = []
    for f in fuel_metrics:
        unified.append(UnifiedInventoryMetrics(
            product_type="fuel",
            product_id=f.fuel_id,
            product_name=f.fuel_name,
            brand=None,
            unit="L",
            current_stock=f.current_stock,
            tank_capacity=f.tank_capacity,
            threshold=f.threshold,
            avg_daily_usage=f.avg_daily_usage,
            usage_std_dev=f.usage_std_dev,
            max_daily_usage=f.max_daily_usage,
            trend=f.trend,
            days_remaining=f.days_remaining,
            safety_stock=f.safety_stock,
            reorder_point=f.reorder_point,
            economic_order_qty=f.economic_order_qty,
            max_stock_level=f.max_stock_level,
            should_reorder=f.should_reorder,
            suggested_reorder_date=f.suggested_reorder_date,
            suggested_quantity=f.suggested_quantity,
            urgency=f.urgency,
            message=f.message,
            cost_of_order=f.cost_of_order,
            days_of_supply_after_reorder=f.days_of_supply_after_reorder,
            fuel_id=f.fuel_id,
            fuel_name=f.fuel_name,
            oil_id=None,
            oil_brand=None
        ))
    oil_metrics = get_oil_inventory_optimization(session)
    unified.extend(oil_metrics)
    order = {"critical":0, "warning":1, "normal":2, "overstocked":3}
    unified.sort(key=lambda x: (order.get(x.urgency,99), x.days_remaining))
    return unified

class AIInventoryMetrics(InventoryMetrics):
    ai_urgency_explanation: str
    ai_demand_insight: str
    ai_purchase_recommendation: str
    ai_risk_factors: List[str]
    ai_action_items: List[str]

@router.get("/inventory-optimization", response_model=List[UnifiedInventoryMetrics])
def get_unified_inventory(session: Session = Depends(get_session)):
    return get_inventory_optimization(session)

@router.get("/inventory-optimization/fuel", response_model=List[InventoryMetrics])
def get_fuel_inventory(session: Session = Depends(get_session)):
    return get_fuel_inventory_optimization(session)

@router.get("/inventory-optimization/oil", response_model=List[UnifiedInventoryMetrics])
def get_oil_inventory(session: Session = Depends(get_session)):
    return get_oil_inventory_optimization(session)

@router.get("/ai-inventory-optimization", response_model=List[AIUnifiedInventoryMetrics])
def get_ai_inventory_optimization(session: Session = Depends(get_session)):
    base_metrics = get_inventory_optimization(session)
    enhanced = []
    for metric in base_metrics:
        data = metric.model_dump()
        if metric.urgency in ["critical", "warning", "overstocked"]:
            ai_data = get_ai_reorder_insights(data)
        else:
            unit = metric.unit
            ai_data = {
                "urgency_explanation": "Stock levels normal",
                "demand_insight": f"Usage stable at {metric.avg_daily_usage}{unit}/day",
                "purchase_recommendation": "No action needed",
                "risk_factors": [],
                "action_items": ["Monitor weekly"]
            }
        enhanced.append(AIUnifiedInventoryMetrics(
            **data,
            ai_urgency_explanation=ai_data["urgency_explanation"],
            ai_demand_insight=ai_data["demand_insight"],
            ai_purchase_recommendation=ai_data["purchase_recommendation"],
            ai_risk_factors=ai_data["risk_factors"],
            ai_action_items=ai_data["action_items"]
        ))
    return enhanced

@router.get("/ai-inventory-optimization/fuel", response_model=List[AIInventoryMetrics])
def get_ai_fuel_inventory(session: Session = Depends(get_session)):
    base_metrics = get_fuel_inventory_optimization(session)
    enhanced = []
    for metric in base_metrics:
        d = metric.model_dump()
        if metric.urgency in ["critical", "warning", "overstocked"]:
            ai_data = get_ai_reorder_insights(d)
        else:
            ai_data = {
                "urgency_explanation": "Stock levels normal",
                "demand_insight": f"Usage stable at {metric.avg_daily_usage}L/day",
                "purchase_recommendation": "No action needed",
                "risk_factors": [],
                "action_items": ["Monitor weekly"]
            }
        enhanced.append(AIInventoryMetrics(**d, ai_urgency_explanation=ai_data["urgency_explanation"], ai_demand_insight=ai_data["demand_insight"], ai_purchase_recommendation=ai_data["purchase_recommendation"], ai_risk_factors=ai_data["risk_factors"], ai_action_items=ai_data["action_items"]))
    return enhanced

@router.get("/ai-inventory-optimization/oil", response_model=List[AIUnifiedInventoryMetrics])
def get_ai_oil_inventory(session: Session = Depends(get_session)):
    base_metrics = get_oil_inventory_optimization(session)
    enhanced = []
    for metric in base_metrics:
        d = metric.model_dump()
        if metric.urgency in ["critical", "warning", "overstocked"]:
            ai_data = get_ai_reorder_insights(d)
        else:
            ai_data = {
                "urgency_explanation": "Stock levels normal",
                "demand_insight": f"Usage stable at {metric.avg_daily_usage}pcs/day",
                "purchase_recommendation": "No action needed",
                "risk_factors": [],
                "action_items": ["Monitor weekly"]
            }
        enhanced.append(AIUnifiedInventoryMetrics(**d, ai_urgency_explanation=ai_data["urgency_explanation"], ai_demand_insight=ai_data["demand_insight"], ai_purchase_recommendation=ai_data["purchase_recommendation"], ai_risk_factors=ai_data["risk_factors"], ai_action_items=ai_data["action_items"]))
    return enhanced

@router.get("/low-stock", response_model=UnifiedLowStockResponse)
def get_unified_low_stock(
    product_type: ProductType = Query("all", description="fuel | oil | all"),
    include_warning: bool = Query(False, description="Include warning level (stock <= reorder_point) not just <= threshold"),
    session: Session = Depends(get_session)
):
    items: List[UnifiedLowStockItem] = []

    if product_type in ("fuel", "all"):
        fuels = session.exec(select(Fuel)).all()
        for fuel in fuels:
            is_low = fuel.actual_liters <= fuel.threshold
            if not is_low and include_warning:
                is_low = fuel.actual_liters <= fuel.threshold * 1.5
            if is_low:
                urgency = "critical" if fuel.actual_liters <= fuel.threshold * 0.5 else "warning"
                items.append(UnifiedLowStockItem(
                    product_type="fuel",
                    product_id=fuel.id,
                    product_name=fuel.name,
                    brand=None,
                    variant=None,
                    unit="L",
                    current_stock=float(fuel.actual_liters),
                    threshold=float(fuel.threshold),
                    capacity=float(fuel.tank_capacity),
                    needs_restock=True,
                    urgency=urgency,
                    days_remaining=None,
                    fuel_id=fuel.id,
                    oil_id=None
                ))

    if product_type in ("oil", "all"):
        oils = session.exec(select(OilProduct)).all()
        for oil in oils:
            is_low = oil.stock <= oil.low_stock_threshold
            if not is_low and include_warning:
                is_low = oil.stock <= oil.low_stock_threshold * 1.5
            if is_low:
                urgency = "critical" if oil.stock <= oil.low_stock_threshold * 0.5 else "warning"
                product_name = f"{oil.brand} {oil.name}" + (f" {oil.variant}" if oil.variant else "")
                items.append(UnifiedLowStockItem(
                    product_type="oil",
                    product_id=oil.id,
                    product_name=product_name,
                    brand=oil.brand,
                    variant=oil.variant,
                    unit="pcs",
                    current_stock=float(oil.stock),
                    threshold=float(oil.low_stock_threshold),
                    capacity=None,
                    needs_restock=True,
                    urgency=urgency,
                    days_remaining=None,
                    fuel_id=None,
                    oil_id=oil.id
                ))

    def sort_key(x):
        urgency_order = 0 if x.urgency == "critical" else 1
        ratio = x.current_stock / x.threshold if x.threshold > 0 else 999
        return (urgency_order, ratio)

    items.sort(key=sort_key)

    fuel_count = sum(1 for i in items if i.product_type == "fuel")
    oil_count = sum(1 for i in items if i.product_type == "oil")

    return UnifiedLowStockResponse(
        total_count=len(items),
        fuel_count=fuel_count,
        oil_count=oil_count,
        items=items
    )