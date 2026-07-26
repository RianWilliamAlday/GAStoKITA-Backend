import os
import json
import sys
import traceback
from datetime import date
from pathlib import Path
from dotenv import load_dotenv
from google import genai
from google.genai import types

BACKEND_ENV = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(BACKEND_ENV)

api_key = os.getenv("GOOGLE_API_KEY")

_client = None
def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=api_key)
    return _client

def get_ai_reorder_insights(fuel_data: dict) -> dict:
    prompt = f"""
You are a fuel inventory advisor for a gas station in the Philippines.

Analyze this fuel data and provide actionable insights.

DATA:
Fuel: {fuel_data['fuel_name']}
Current Stock: {fuel_data['current_stock']}L / {fuel_data['tank_capacity']}L capacity
Average Daily Usage: {fuel_data['avg_daily_usage']}L (std dev: {fuel_data['usage_std_dev']}L)
Trend: {fuel_data['trend']} over last 14 days
Days Remaining: {fuel_data['days_remaining']}
Reorder Point: {fuel_data['reorder_point']}L
Safety Stock: {fuel_data['safety_stock']}L
Lead Time: 3 days
Current Date: {date.today().isoformat()}

Return JSON with these exact keys:
{{
  "urgency_explanation": "1-2 sentences why this urgency level. Mention specific numbers.",
  "demand_insight": "1 sentence on what the trend + std_dev means for this fuel.",
  "purchase_recommendation": "1 sentence: buy/delay + quantity + reasoning",
  "risk_factors": ["list", "of", "2-3 risks"],
  "action_items": ["list", "of", "2-3 concrete steps for manager"]
}}
"""
    try:
        client = _get_client()
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                response_mime_type="application/json"
            )
        )
        return json.loads(resp.text)

    except Exception as e:
        print("\n=== GEMINI CALL FAILED ===", file=sys.stderr, flush=True)
        traceback.print_exc()
        try:
            print("Response snippet:", resp.text[:300], file=sys.stderr)
        except:
            pass
        return {
            "urgency_explanation": f"Stock at {fuel_data['current_stock']}L vs reorder point {fuel_data['reorder_point']}L",
            "demand_insight": f"Trend is {fuel_data['trend']}",
            "purchase_recommendation": "Review metrics manually",
            "risk_factors": ["AI analysis unavailable"],
            "action_items": ["Check dashboard"]
        }