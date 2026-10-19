from pydantic import BaseModel, Field
from typing import Optional, List, Literal, Dict
from datetime import datetime, date
from enum import Enum

class ProductType(str, Enum):
    FUEL = "fuel"
    OIL = "oil"
    ALL = "all"
    MULTI = "multi"

class FuelBatchResponse(BaseModel):
    id: int
    fuel_id: int
    liters_initial: float
    liters_remaining: float
    cost_per_liter: float
    selling_price: float
    supplier: Optional[str] = None
    restocked_by: Optional[str] = None
    restocked_at: datetime

class FuelTypeResponse(BaseModel):
    id: int
    name: str
    price: float
    actual_liters: float
    tank_capacity: float
    display_cm: int
    display_liters: int
    display_percentage: float
    threshold: float
    needs_restock: bool
    fifo_price: Optional[float] = None
    oldest_batch_price: Optional[float] = None
    newest_batch_price: Optional[float] = None
    active_batches: Optional[int] = None
    batches: List[FuelBatchResponse] = []

class Pump(BaseModel):
    id: int
    name: str
    fuel_type_id: int
    status: str

class DispenseRequest(BaseModel):
    pump_id: int
    amount_liters: float

class PriceUpdate(BaseModel):
    price: float

class RestockRequest(BaseModel):
    liters_added: float
    cost: float
    supplier: Optional[str] = None
    selling_price: float

class ThresholdUpdate(BaseModel):
    threshold: float

class FifoSaleDetail(BaseModel):
    batch_id: int
    liters_consumed: float
    price_per_liter: float
    total_amount: float
    restocked_at: Optional[datetime] = None

class SaleCreate(BaseModel):
    pump_id: int
    liters_sold: float
    attendant_name: str
    payment_method: str = "cash"
    amount_paid: float = 0

class SaleResponse(BaseModel):
    id: int
    product_type: Literal["fuel"] = "fuel"
    fuel_name: str
    pump_name: str
    attendant_name: str
    recorded_by: str
    liters_sold: float
    price_per_liter: float
    total_amount: float
    amount_paid: float
    change_given: float
    receipt_no: str
    payment_method: str
    sold_at: datetime
    fifo_breakdown: Optional[List[FifoSaleDetail]] = None
    is_split_price: bool = False
    payment_status: Optional[str] = None
    checkout_url: Optional[str] = None
    checkout_id: Optional[str] = None

class AttendantSalesSummary(BaseModel):
    attendant_name: str
    total_sales: float
    total_liters: float
    transaction_count: int
    by_fuel: dict

class SaleHistoryItem(BaseModel):
    id: int
    sold_at: datetime
    fuel_id: int
    pump_id: int
    attendant_name: str
    liters_sold: float
    price_per_liter: float
    total_amount: float
    amount_paid: float
    change_given: float
    receipt_no: Optional[str] = None
    payment_method: str
    recorded_by: str
    fuel_name: Optional[str] = None
    pump_name: Optional[str] = None
    fifo_breakdown: Optional[List[FifoSaleDetail]] = None

class SalesHistoryResponse(BaseModel):
    sales: List[SaleHistoryItem]
    total_count: int
    total_amount: float
    total_liters: float
    page: int
    page_size: int

class SalesFilter(BaseModel):
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    fuel_id: Optional[int] = None
    attendant_name: Optional[str] = None
    pump_id: Optional[int] = None
    payment_method: Optional[str] = None
    page: int = 1
    page_size: int = 50

class OilSaleCreate(BaseModel):
    oil_id: int
    quantity: int
    payment_method: str = "cash"
    attendant_name: Optional[str] = None
    amount_paid: float = 0

class OilSaleResponse(BaseModel):
    id: int
    product_type: Literal["oil"] = "oil"
    product_name: str
    brand: str
    quantity: int
    price_per_unit: float
    total_amount: float
    amount_paid: float
    change_given: float
    receipt_no: str
    payment_method: str
    attendant_name: str
    sold_at: datetime
    recorded_by: str
    remaining_stock: int
    payment_status: Optional[str] = None
    checkout_url: Optional[str] = None
    checkout_id: Optional[str] = None

class OilSaleHistoryItem(BaseModel):
    id: int
    oil_product_id: int
    product_name: str
    brand: str
    quantity: int
    price_per_unit: float
    total_amount: float
    amount_paid: float
    change_given: float
    receipt_no: Optional[str] = None
    payment_method: str
    attendant_name: Optional[str] = None
    sold_at: datetime
    recorded_by: str

class OilSalesHistoryResponse(BaseModel):
    sales: List[OilSaleHistoryItem]
    total_count: int
    total_amount: float
    total_quantity: int
    page: int
    page_size: int

class UnifiedSaleItem(BaseModel):
    id: int
    product_type: ProductType
    product_name: str
    quantity: float
    unit: Literal["L", "pcs", "items"]
    price_per_unit: float
    total_amount: float
    amount_paid: float
    change_given: float
    receipt_no: Optional[str] = None
    payment_method: str
    attendant_name: str
    sold_at: datetime
    recorded_by: str
    fuel_id: Optional[int] = None
    pump_id: Optional[int] = None
    pump_name: Optional[str] = None
    oil_id: Optional[int] = None
    brand: Optional[str] = None

class UnifiedHistoryResponse(BaseModel):
    sales: List[UnifiedSaleItem]
    total_count: int
    total_amount: float
    total_liters: float
    total_oil_pcs: int
    total_fuel_count: int = 0
    total_oil_count: int = 0
    page: int
    page_size: int

class InventoryMetrics(BaseModel):
    fuel_id: int
    fuel_name: str
    current_stock: float
    tank_capacity: float
    threshold: float
    avg_daily_usage: float
    usage_std_dev: float
    max_daily_usage: float
    trend: Literal["increasing", "decreasing", "stable"]
    days_remaining: float
    safety_stock: float
    reorder_point: float
    economic_order_qty: float
    max_stock_level: float
    should_reorder: bool
    suggested_reorder_date: date
    suggested_quantity: float
    urgency: Literal["critical", "warning", "normal", "overstocked"]
    message: str
    cost_of_order: float
    days_of_supply_after_reorder: float

class AIInventoryMetrics(InventoryMetrics):
    ai_urgency_explanation: str
    ai_demand_insight: str
    ai_purchase_recommendation: str
    ai_risk_factors: List[str]
    ai_action_items: List[str]

class UnifiedInventoryMetrics(BaseModel):
    product_type: Literal["fuel", "oil"]
    product_id: int
    product_name: str
    brand: Optional[str] = None
    unit: Literal["L", "pcs"]
    current_stock: float
    tank_capacity: float
    threshold: float
    avg_daily_usage: float
    usage_std_dev: float
    max_daily_usage: float
    trend: Literal["increasing", "decreasing", "stable"]
    days_remaining: float
    safety_stock: float
    reorder_point: float
    economic_order_qty: float
    max_stock_level: float
    should_reorder: bool
    suggested_reorder_date: date
    suggested_quantity: float
    urgency: Literal["critical", "warning", "normal", "overstocked"]
    message: str
    cost_of_order: float
    days_of_supply_after_reorder: float
    fuel_id: Optional[int] = None
    fuel_name: Optional[str] = None
    oil_id: Optional[int] = None
    oil_brand: Optional[str] = None

class AIUnifiedInventoryMetrics(UnifiedInventoryMetrics):
    ai_urgency_explanation: str
    ai_demand_insight: str
    ai_purchase_recommendation: str
    ai_risk_factors: List[str]
    ai_action_items: List[str]

class UnifiedLowStockItem(BaseModel):
    product_type: Literal["fuel", "oil"]
    product_id: int
    product_name: str
    brand: Optional[str] = None
    variant: Optional[str] = None
    unit: Literal["L", "pcs"]
    current_stock: float
    threshold: float
    capacity: Optional[float] = None
    needs_restock: bool = True
    urgency: Literal["critical", "warning"]
    days_remaining: Optional[float] = None
    fuel_id: Optional[int] = None
    oil_id: Optional[int] = None
    current_fifo_price: Optional[float] = None

class UnifiedLowStockResponse(BaseModel):
    total_count: int
    fuel_count: int
    oil_count: int
    items: List[UnifiedLowStockItem]

class UnifiedExpenseItem(BaseModel):
    id: int
    source_type: Literal["operational", "fuel_restock", "oil_restock"]
    category: str
    description: str
    amount: float
    expense_date: datetime
    recorded_by: Optional[str] = None
    supplier: Optional[str] = None

class MultiFuelItem(BaseModel):
    pump_id: int
    liters_sold: float = Field(..., gt=0)

class MultiOilItem(BaseModel):
    oil_id: int
    quantity: int = Field(..., gt=0)

class MultiSaleCreate(BaseModel):
    attendant_name: str
    payment_method: str = Field(..., description="cash or gcash")
    amount_paid: float = Field(0.0, ge=0)
    fuel_items: List[MultiFuelItem] = []
    oil_items: List[MultiOilItem] = []

class MultiSaleResponse(BaseModel):
    transaction_ref: str
    attendant_name: str
    payment_method: str
    payment_status: str
    total_amount: float
    amount_paid: float
    change_given: float
    sold_at: datetime
    fuel_sales: List[SaleResponse] = []
    oil_sales: List[OilSaleResponse] = []
    checkout_url: Optional[str] = None
    checkout_id: Optional[str] = None

class FuelLitersBreakdown(BaseModel):
    fuel_id: int
    fuel_name: str
    liters_sold: float
    revenue: float
    transaction_count: int

class DailyLitersSummaryResponse(BaseModel):
    date: str
    period_start: datetime
    period_end: datetime
    total_liters_sold: float
    total_revenue: float
    total_transactions: int
    fuel_breakdown: List[FuelLitersBreakdown]