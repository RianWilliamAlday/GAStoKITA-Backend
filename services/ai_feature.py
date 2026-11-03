import os
import json
import sys
import traceback
from datetime import date
from pathlib import Path
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

BACKEND_ENV = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(BACKEND_ENV)

api_key = os.getenv("GOOGLE_API_KEY")

_client = None
def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=api_key)
    return _client

class ReorderInsightsResponse(BaseModel):
    urgency_explanation: str = Field(description="1-2 sentences explaining the urgency level with specific numbers.")
    demand_insight: str = Field(description="1 sentence on what the trend and standard deviation imply.")
    purchase_recommendation: str = Field(description="1 sentence with buy/delay advice, quantity, and reasoning.")
    risk_factors: list[str] = Field(description="List of 2-3 operational or financial risk factors.")
    action_items: list[str] = Field(description="List of 2-3 concrete steps for the station manager.")

def get_ai_reorder_insights(item_data: dict) -> dict:
    product_name = item_data.get("product_name") or item_data.get("fuel_name") or "Inventory Item"
    product_type = item_data.get("product_type", "fuel")
    unit = item_data.get("unit") or ("L" if product_type == "fuel" else "pcs")
    capacity = item_data.get("tank_capacity") or item_data.get("max_stock_level") or 0.0

    prompt = f"""
You are an expert inventory advisor for a gas station and auto service business in the Philippines.

Analyze the following {product_type} product metrics and provide clear, actionable inventory advice.

METRICS:
- Product Name: {product_name} ({product_type.upper()})
- Current Stock: {item_data.get('current_stock', 0)} {unit} / {capacity} {unit} capacity
- Average Daily Usage: {item_data.get('avg_daily_usage', 0)} {unit}/day (Std Dev: {item_data.get('usage_std_dev', 0)} {unit})
- 14-Day Trend: {item_data.get('trend', 'stable')}
- Days Remaining: {item_data.get('days_remaining', 0)} days
- Reorder Point: {item_data.get('reorder_point', 0)} {unit}
- Safety Stock: {item_data.get('safety_stock', 0)} {unit}
- Delivery Lead Time: 3-5 days
- Date: {date.today().isoformat()}
"""
    resp = None
    try:
        client = _get_client()
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                response_mime_type="application/json",
                response_schema=ReorderInsightsResponse,
            )
        )
        data = json.loads(resp.text)
        return {
            "urgency_explanation": data.get("urgency_explanation") or f"Stock is at {item_data.get('current_stock', 0)} {unit}.",
            "demand_insight": data.get("demand_insight") or f"Recent trend is {item_data.get('trend', 'stable')}.",
            "purchase_recommendation": data.get("purchase_recommendation") or "Review stock level.",
            "risk_factors": [r for r in (data.get("risk_factors") or []) if r],
            "action_items": [a for a in (data.get("action_items") or []) if a],
        }

    except Exception as e:
        print("\n=== GEMINI CALL FAILED ===", file=sys.stderr, flush=True)
        traceback.print_exc()
        
        if resp and hasattr(resp, "text"):
            print("Response snippet:", (resp.text or "")[:300], file=sys.stderr)
        return {
            "urgency_explanation": f"Stock is at {item_data.get('current_stock', 0)} {unit} relative to reorder point of {item_data.get('reorder_point', 0)} {unit}.",
            "demand_insight": f"Recent 14-day trend is classified as {item_data.get('trend', 'unknown')}.",
            "purchase_recommendation": "Manual review required due to temporary insight service disruption.",
            "risk_factors": ["Automated AI analytics temporarily unavailable"],
            "action_items": ["Verify inventory levels manually", "Check pending purchase orders in system"],
            "is_fallback": True
        }