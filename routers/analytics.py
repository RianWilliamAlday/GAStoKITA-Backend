from typing import Optional, Literal
from datetime import datetime, timedelta
from collections import defaultdict
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, text
from db.database import get_session

router = APIRouter(prefix="/analytics", tags=["Analytics"])

ProductType = Literal["fuel", "oil", "all"]

@router.get("/peak-hours")
def get_peak_hours(
    days: int = Query(30, description="Last N days to analyze"),
    fuel_id: Optional[int] = None,
    oil_id: Optional[int] = None,
    product_type: ProductType = Query("fuel", description="fuel | oil | all"),
    session: Session = Depends(get_session)
):
    since_date = datetime.now() - timedelta(days=days)

    fuel_hourly = {}
    if product_type in ("fuel", "all"):
        fuel_sql = text("""
            SELECT CAST(strftime('%H', sold_at) AS INTEGER) as hour,
                   COUNT(*) as cnt,
                   COALESCE(SUM(total_amount),0) as revenue,
                   COALESCE(SUM(liters_sold),0) as qty
            FROM sale
            WHERE sold_at >= :since_date
              AND (:fuel_id IS NULL OR fuel_id = :fuel_id)
            GROUP BY hour
        """)
        rows = session.execute(fuel_sql, {"since_date": since_date, "fuel_id": fuel_id}).fetchall()
        for h, cnt, rev, qty in rows:
            fuel_hourly[h] = {"count": cnt, "revenue": rev, "qty": qty}

    oil_hourly = {}
    if product_type in ("oil", "all"):
        oil_sql = text("""
            SELECT CAST(strftime('%H', sold_at) AS INTEGER) as hour,
                   COUNT(*) as cnt,
                   COALESCE(SUM(total_amount),0) as revenue,
                   COALESCE(SUM(quantity),0) as qty
            FROM oil_sale
            WHERE sold_at >= :since_date
              AND (:oil_id IS NULL OR oil_product_id = :oil_id)
            GROUP BY hour
        """)
        rows = session.execute(oil_sql, {"since_date": since_date, "oil_id": oil_id}).fetchall()
        for h, cnt, rev, qty in rows:
            oil_hourly[h] = {"count": cnt, "revenue": rev, "qty": qty}

    hourly_data = []
    for h in range(24):
        f = fuel_hourly.get(h, {"count":0,"revenue":0,"qty":0})
        o = oil_hourly.get(h, {"count":0,"revenue":0,"qty":0})

        if product_type == "fuel":
            total_count = f["count"]
            total_revenue = f["revenue"]
            total_qty = f["qty"]
        elif product_type == "oil":
            total_count = o["count"]
            total_revenue = o["revenue"]
            total_qty = o["qty"]
        else:
            total_count = f["count"] + o["count"]
            total_revenue = f["revenue"] + o["revenue"]
            total_qty = f["qty"] + o["qty"]

        hourly_data.append({
            "hour": h,
            "label": f"{h:02d}:00",
            "transaction_count": total_count,
            "total_revenue": round(total_revenue,2),
            "total_liters": round(f["qty"],2),
            "total_oil_pcs": o["qty"],
            "total_quantity_mixed": total_qty,
            "breakdown": {"fuel": f, "oil": o} if product_type=="all" else None
        })

    max_window, max_count = None, 0
    for i in range(22):
        wc = sum(hourly_data[j]["transaction_count"] for j in range(i, i+3))
        if wc > max_count:
            max_count, max_window = wc, f"{i:02d}:00 - {i+3:02d}:00"

    avg = sum(h["transaction_count"] for h in hourly_data) / 24 if hourly_data else 0
    for h in hourly_data:
        if h["transaction_count"] >= avg * 1.8:
            h["staffing_level"] = "High - 2 Cashiers Recommended"
        elif h["transaction_count"] >= avg * 1.2:
            h["staffing_level"] = "Medium - 1 + Standby"
        else:
            h["staffing_level"] = "Low - 1 Cashier"

    busiest = max(hourly_data, key=lambda x: x["transaction_count"]) if hourly_data else None

    return {
        "period_days": days,
        "product_type": product_type,
        "busiest_hour": busiest,
        "peak_window_3h": max_window,
        "peak_window_transactions": max_count,
        "hourly_breakdown": hourly_data,
        "insight": f"Peak demand ({product_type}) is {max_window} with {max_count} transactions. Busiest single hour is {busiest['label'] if busiest else 'N/A'}."
    }

@router.get("/heatmap")
def get_demand_heatmap(
    days: int = 30,
    product_type: ProductType = Query("fuel"),
    session: Session = Depends(get_session)
):
    since_date = datetime.now() - timedelta(days=days)

    data = []
    if product_type in ("fuel","all"):
        sql_fuel = text("""
            SELECT CAST(strftime('%w', sold_at) AS INTEGER) as dow,
                   CAST(strftime('%H', sold_at) AS INTEGER) as hour,
                   COUNT(*) as cnt
            FROM sale WHERE sold_at >= :since_date GROUP BY dow, hour
        """)
        for r in session.execute(sql_fuel, {"since_date": since_date}).fetchall():
            data.append({"day": r[0], "hour": r[1], "count": r[2], "type": "fuel"})

    if product_type in ("oil","all"):
        sql_oil = text("""
            SELECT CAST(strftime('%w', sold_at) AS INTEGER) as dow,
                   CAST(strftime('%H', sold_at) AS INTEGER) as hour,
                   COUNT(*) as cnt
            FROM oil_sale WHERE sold_at >= :since_date GROUP BY dow, hour
        """)
        for r in session.execute(sql_oil, {"since_date": since_date}).fetchall():
            data.append({"day": r[0], "hour": r[1], "count": r[2], "type": "oil"})

    if product_type == "all":
        merged = {}
        for d in data:
            key = (d["day"], d["hour"])
            merged[key] = merged.get(key, 0) + d["count"]
        merged_data = [{"day": k[0], "hour": k[1], "count": v} for k,v in merged.items()]
        return {"product_type": product_type, "data": merged_data, "day_labels": ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"]}
    
    return {
        "product_type": product_type,
        "data": [{"day": r["day"], "hour": r["hour"], "count": r["count"]} for r in data],
        "day_labels": ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"]
    }

@router.get("/profit-margins/fuel")
def get_fuel_profit_margins(days: int = 30, session: Session = Depends(get_session)):
    since = datetime.now() - timedelta(days=days)
    sql = text("""
        WITH fuel_costs AS (
            SELECT fuel_id, SUM(cost) as total_cost, SUM(liters_added) as total_liters_added,
                   CASE WHEN SUM(liters_added) > 0 THEN SUM(cost)/SUM(liters_added) ELSE 0 END as avg_cost_per_liter
            FROM restocklog WHERE restocked_at >= :since GROUP BY fuel_id
        ),
        fuel_sales AS (
            SELECT fuel_id, SUM(liters_sold) as liters_sold, SUM(total_amount) as revenue, AVG(price_per_liter) as avg_selling_price
            FROM sale WHERE sold_at >= :since GROUP BY fuel_id
        )
        SELECT f.id, f.name, COALESCE(fs.revenue,0) as revenue, COALESCE(fs.liters_sold,0) as liters_sold,
               COALESCE(fc.avg_cost_per_liter,0) as avg_cost, COALESCE(fs.avg_selling_price, f.price) as selling_price,
               COALESCE(fs.revenue,0) - (COALESCE(fs.liters_sold,0) * COALESCE(fc.avg_cost_per_liter,0)) as gross_profit
        FROM fuel f LEFT JOIN fuel_sales fs ON fs.fuel_id = f.id LEFT JOIN fuel_costs fc ON fc.fuel_id = f.id
    """)
    rows = session.execute(sql, {"since": since}).fetchall()
    result = []
    for r in rows:
        revenue = r[2]; profit = r[6]
        margin = (profit / revenue * 100) if revenue > 0 else 0
        result.append({
            "product_type": "fuel",
            "fuel_id": r[0], "fuel_name": r[1],
            "revenue": round(revenue,2), "liters_sold": round(r[3],2),
            "avg_cost_per_liter": round(r[4],2), "avg_selling_price": round(r[5],2),
            "gross_profit": round(profit,2), "margin_percent": round(margin,2)
        })
    return result

@router.get("/profit-margins/oil")
def get_oil_profit_margins(days: int = 30, session: Session = Depends(get_session)):
    since = datetime.now() - timedelta(days=days)
    sql = text("""
        WITH oil_costs AS (
            SELECT oil_product_id, SUM(total_cost) as total_cost, SUM(quantity_added) as total_qty,
                   CASE WHEN SUM(quantity_added) > 0 THEN SUM(total_cost)/SUM(quantity_added) ELSE 0 END as avg_cost_per_unit
            FROM oil_restock_log WHERE restocked_at >= :since GROUP BY oil_product_id
        ),
        oil_sales AS (
            SELECT oil_product_id, SUM(quantity) as qty_sold, SUM(total_amount) as revenue, AVG(price_per_unit) as avg_selling_price
            FROM oil_sale WHERE sold_at >= :since GROUP BY oil_product_id
        )
        SELECT op.id, op.brand, op.name, COALESCE(os.revenue,0) as revenue, COALESCE(os.qty_sold,0) as qty_sold,
               COALESCE(oc.avg_cost_per_unit,0) as avg_cost, COALESCE(os.avg_selling_price, op.price) as selling_price,
               COALESCE(os.revenue,0) - (COALESCE(os.qty_sold,0) * COALESCE(oc.avg_cost_per_unit,0)) as gross_profit
        FROM oil_product op LEFT JOIN oil_sales os ON os.oil_product_id = op.id LEFT JOIN oil_costs oc ON oc.oil_product_id = op.id
    """)
    rows = session.execute(sql, {"since": since}).fetchall()
    result = []
    for r in rows:
        revenue = r[3]; profit = r[7]
        margin = (profit / revenue * 100) if revenue > 0 else 0
        result.append({
            "product_type": "oil",
            "oil_id": r[0], "brand": r[1], "oil_name": r[2],
            "revenue": round(revenue,2), "quantity_sold": int(r[4]),
            "avg_cost_per_unit": round(r[5],2), "avg_selling_price": round(r[6],2),
            "gross_profit": round(profit,2), "margin_percent": round(margin,2)
        })
    return result

@router.get("/profit-margins/unified")
def get_unified_profit_margins(days: int = 30, session: Session = Depends(get_session)):
    fuel = get_fuel_profit_margins(days, session)
    oil = get_oil_profit_margins(days, session)
    fuel_rev = sum(f["revenue"] for f in fuel)
    fuel_profit = sum(f["gross_profit"] for f in fuel)
    oil_rev = sum(o["revenue"] for o in oil)
    oil_profit = sum(o["gross_profit"] for o in oil)
    total_rev = fuel_rev + oil_rev
    total_profit = fuel_profit + oil_profit
    return {
        "period_days": days,
        "summary": {
            "total_revenue": round(total_rev,2),
            "total_profit": round(total_profit,2),
            "total_margin_percent": round((total_profit/total_rev*100) if total_rev else 0,2),
            "fuel": {"revenue": round(fuel_rev,2), "profit": round(fuel_profit,2), "margin": round((fuel_profit/fuel_rev*100) if fuel_rev else 0,2)},
            "oil": {"revenue": round(oil_rev,2), "profit": round(oil_profit,2), "margin": round((oil_profit/oil_rev*100) if oil_rev else 0,2)}
        },
        "fuel_breakdown": fuel,
        "oil_breakdown": oil
    }

@router.get("/revenue/summary")
def get_revenue_summary(days: int = 30, session: Session = Depends(get_session)):
    since = datetime.now() - timedelta(days=days)
    fuel_sql = text("SELECT COALESCE(SUM(total_amount),0), COALESCE(SUM(liters_sold),0), COUNT(*) FROM sale WHERE sold_at >= :since")
    oil_sql = text("SELECT COALESCE(SUM(total_amount),0), COALESCE(SUM(quantity),0), COUNT(*) FROM oil_sale WHERE sold_at >= :since")
    exp_sql = text("SELECT COALESCE(SUM(amount),0) FROM expense WHERE expense_date >= :since")

    f_rev, f_liters, f_cnt = session.execute(fuel_sql, {"since": since}).one()
    o_rev, o_qty, o_cnt = session.execute(oil_sql, {"since": since}).one()
    exp_total = session.execute(exp_sql, {"since": since}).scalar() or 0

    return {
        "period_days": days,
        "fuel": {"revenue": round(f_rev,2), "liters_sold": round(f_liters,2), "transactions": f_cnt},
        "oil": {"revenue": round(o_rev,2), "quantity_sold": int(o_qty), "transactions": o_cnt},
        "combined": {"revenue": round(f_rev+o_rev,2), "transactions": f_cnt+o_cnt, "expenses": round(exp_total,2), "net": round((f_rev+o_rev)-exp_total,2)}
    }

@router.get("/oil/top-selling")
def get_top_oils(days: int = 30, limit: int = 5, session: Session = Depends(get_session)):
    since = datetime.now() - timedelta(days=days)
    sql = text("""
        SELECT op.id, op.brand, op.name, SUM(os.quantity) as total_qty, SUM(os.total_amount) as total_rev
        FROM oil_sale os JOIN oil_product op ON op.id = os.oil_product_id
        WHERE os.sold_at >= :since
        GROUP BY op.id, op.brand, op.name
        ORDER BY total_qty DESC LIMIT :limit
    """)
    rows = session.execute(sql, {"since": since, "limit": limit}).fetchall()
    return [{"oil_id": r[0], "brand": r[1], "name": r[2], "quantity_sold": int(r[3]), "revenue": round(r[4],2)} for r in rows]

@router.get("/attendants/ranking")
def get_attendant_rankings(
    days: int = Query(30, description="Last N days"),
    product_type: ProductType = Query("all", description="fuel | oil | all"),
    sort_by: str = Query("revenue", description="revenue | liters | transactions | oil_pcs | avg_ticket"),
    limit: int = Query(10, ge=1, le=50),
    include_breakdown: bool = Query(False, description="Include fuel & payment breakdown per attendant"),
    session: Session = Depends(get_session)
):
    """
    Rank attendants by sales performance.
    - Combines fuel + oil sales (based on attendant_name)
    - Sortable by revenue, liters, transactions, oil_pcs, avg_ticket
    - Returns podium ranking with fuel/oil breakdown
    Example: /analytics/attendants/ranking?days=7&product_type=all&sort_by=revenue
    """
    since = datetime.now() - timedelta(days=days)

    fuel_stats = {}
    if product_type in ("fuel", "all"):
        fuel_sql = text("""
            SELECT 
                attendant_name,
                COUNT(*) as txn_count,
                COALESCE(SUM(total_amount),0) as revenue,
                COALESCE(SUM(liters_sold),0) as liters,
                COALESCE(AVG(total_amount),0) as avg_ticket,
                MAX(sold_at) as last_sale
            FROM sale
            WHERE sold_at >= :since
              AND attendant_name IS NOT NULL
              AND attendant_name != ''
            GROUP BY attendant_name
        """)
        for row in session.execute(fuel_sql, {"since": since}).fetchall():
            name = row[0]
            fuel_stats[name] = {
                "fuel_transactions": row[1],
                "fuel_revenue": row[2],
                "fuel_liters": row[3],
                "fuel_avg_ticket": row[4],
                "fuel_last_sale": row[5],
            }

    oil_stats = {}
    if product_type in ("oil", "all"):
        oil_sql = text("""
            SELECT 
                attendant_name,
                COUNT(*) as txn_count,
                COALESCE(SUM(total_amount),0) as revenue,
                COALESCE(SUM(quantity),0) as qty,
                COALESCE(AVG(total_amount),0) as avg_ticket,
                MAX(sold_at) as last_sale
            FROM oil_sale
            WHERE sold_at >= :since
              AND attendant_name IS NOT NULL
              AND attendant_name != ''
            GROUP BY attendant_name
        """)
        for row in session.execute(oil_sql, {"since": since}).fetchall():
            name = row[0]
            oil_stats[name] = {
                "oil_transactions": row[1],
                "oil_revenue": row[2],
                "oil_qty": row[3],
                "oil_avg_ticket": row[4],
                "oil_last_sale": row[5],
            }

    all_names = set(fuel_stats.keys()) | set(oil_stats.keys())
    rankings = []

    for name in all_names:
        f = fuel_stats.get(name, {"fuel_transactions":0, "fuel_revenue":0, "fuel_liters":0, "fuel_avg_ticket":0, "fuel_last_sale": None})
        o = oil_stats.get(name, {"oil_transactions":0, "oil_revenue":0, "oil_qty":0, "oil_avg_ticket":0, "oil_last_sale": None})

        total_txn = f["fuel_transactions"] + o["oil_transactions"]
        total_rev = f["fuel_revenue"] + o["oil_revenue"]
        total_liters = f["fuel_liters"]
        total_oil = o["oil_qty"]
        avg_ticket = total_rev / total_txn if total_txn > 0 else 0

        last_sales = [d for d in [f["fuel_last_sale"], o["oil_last_sale"]] if d]
        last_sale = max(last_sales) if last_sales else None

        entry = {
            "attendant_name": name,
            "total_revenue": round(total_rev,2),
            "total_liters": round(total_liters,2),
            "total_oil_pcs": int(total_oil),
            "transaction_count": total_txn,
            "fuel_transactions": f["fuel_transactions"],
            "oil_transactions": o["oil_transactions"],
            "fuel_revenue": round(f["fuel_revenue"],2),
            "oil_revenue": round(o["oil_revenue"],2),
            "avg_ticket": round(avg_ticket,2),
            "avg_liters_per_transaction": round(total_liters / f["fuel_transactions"],2) if f["fuel_transactions"]>0 else 0,
            "last_sale_at": last_sale,
        }

        if include_breakdown:
            fuel_break_sql = text("""
                SELECT f.name, COUNT(*) as cnt, SUM(s.liters_sold) as liters, SUM(s.total_amount) as rev
                FROM sale s JOIN fuel f ON f.id = s.fuel_id
                WHERE s.attendant_name = :name AND s.sold_at >= :since
                GROUP BY f.name
            """)
            fb = session.execute(fuel_break_sql, {"name": name, "since": since}).fetchall()
            entry["fuel_breakdown"] = [{"fuel_name": r[0], "transactions": r[1], "liters": round(r[2],2), "revenue": round(r[3],2)} for r in fb]

            pay_sql_fuel = text("""
                SELECT payment_method, COUNT(*) as cnt, SUM(total_amount) as rev
                FROM sale WHERE attendant_name = :name AND sold_at >= :since GROUP BY payment_method
            """)
            pay_sql_oil = text("""
                SELECT payment_method, COUNT(*) as cnt, SUM(total_amount) as rev
                FROM oil_sale WHERE attendant_name = :name AND sold_at >= :since GROUP BY payment_method
            """)
            pay_f = {r[0]: {"count": r[1], "revenue": r[2]} for r in session.execute(pay_sql_fuel, {"name": name, "since": since}).fetchall()}
            pay_o = {r[0]: {"count": r[1], "revenue": r[2]} for r in session.execute(pay_sql_oil, {"name": name, "since": since}).fetchall()}
            merged_pay = {}
            for method in set(list(pay_f.keys()) + list(pay_o.keys())):
                cf = pay_f.get(method, {"count":0,"revenue":0})
                co = pay_o.get(method, {"count":0,"revenue":0})
                merged_pay[method] = {"transactions": cf["count"]+co["count"], "revenue": round(cf["revenue"]+co["revenue"],2)}
            entry["payment_breakdown"] = merged_pay

        rankings.append(entry)

    sort_key_map = {
        "revenue": lambda x: x["total_revenue"],
        "liters": lambda x: x["total_liters"],
        "transactions": lambda x: x["transaction_count"],
        "oil_pcs": lambda x: x["total_oil_pcs"],
        "avg_ticket": lambda x: x["avg_ticket"],
    }
    key_fn = sort_key_map.get(sort_by, sort_key_map["revenue"])
    rankings.sort(key=key_fn, reverse=True)

    for idx, r in enumerate(rankings, 1):
        r["rank"] = idx
        if idx == 1: r["medal"] = "🥇"
        elif idx == 2: r["medal"] = "🥈"
        elif idx == 3: r["medal"] = "🥉"
        else: r["medal"] = None

    limited = rankings[:limit]

    total_rev_all = sum(r["total_revenue"] for r in rankings)
    total_txn_all = sum(r["transaction_count"] for r in rankings)

    return {
        "period_days": days,
        "product_type": product_type,
        "sort_by": sort_by,
        "total_attendants": len(rankings),
        "total_revenue_all": round(total_rev_all,2),
        "total_transactions_all": total_txn_all,
        "rankings": limited,
        "top_performer": limited[0] if limited else None,
        "insight": f"Top performer is {limited[0]['attendant_name']} with ₱{limited[0]['total_revenue']} revenue in last {days} days." if limited else "No sales in period."
    }

@router.get("/attendants/leaderboard")
def get_attendant_leaderboard(
    days: int = Query(7, description="Period: 1=today, 7=week, 30=month"),
    session: Session = Depends(get_session)
):
    """Podium-style top 3 for quick dashboard widget"""
    data = get_attendant_rankings(days=days, product_type="all", sort_by="revenue", limit=3, include_breakdown=False, session=session)
    rankings = data["rankings"]
    return {
        "period_days": days,
        "podium": rankings,
        "total_attendants": data["total_attendants"],
        "top_1": rankings[0] if len(rankings)>0 else None,
        "top_2": rankings[1] if len(rankings)>1 else None,
        "top_3": rankings[2] if len(rankings)>2 else None,
    }

@router.get("/attendants/{attendant_name}/performance")
def get_attendant_performance(
    attendant_name: str,
    days: int = Query(30),
    session: Session = Depends(get_session)
):
    """Detailed performance for a single attendant: daily trend, fuel mix, payment mix, hourly pattern"""
    since = datetime.now() - timedelta(days=days)

    fuel_sql = text("""
        SELECT COUNT(*), COALESCE(SUM(total_amount),0), COALESCE(SUM(liters_sold),0), COALESCE(AVG(total_amount),0)
        FROM sale WHERE attendant_name = :name AND sold_at >= :since
    """)
    oil_sql = text("""
        SELECT COUNT(*), COALESCE(SUM(total_amount),0), COALESCE(SUM(quantity),0), COALESCE(AVG(total_amount),0)
        FROM oil_sale WHERE attendant_name = :name AND sold_at >= :since
    """)
    f_cnt, f_rev, f_liters, f_avg = session.execute(fuel_sql, {"name": attendant_name, "since": since}).one()
    o_cnt, o_rev, o_qty, o_avg = session.execute(oil_sql, {"name": attendant_name, "since": since}).one()

    daily_fuel = text("""
        SELECT DATE(sold_at) as d, COUNT(*) as cnt, SUM(total_amount) as rev, SUM(liters_sold) as liters
        FROM sale WHERE attendant_name = :name AND sold_at >= :since GROUP BY DATE(sold_at) ORDER BY d ASC
    """)
    daily_oil = text("""
        SELECT DATE(sold_at) as d, COUNT(*) as cnt, SUM(total_amount) as rev, SUM(quantity) as qty
        FROM oil_sale WHERE attendant_name = :name AND sold_at >= :since GROUP BY DATE(sold_at) ORDER BY d ASC
    """)
    daily_data = {}
    for r in session.execute(daily_fuel, {"name": attendant_name, "since": since}).fetchall():
        d = str(r[0]); daily_data[d] = {"date": d, "fuel_txn": r[1], "fuel_rev": r[2], "fuel_liters": r[3], "oil_txn":0, "oil_rev":0, "oil_qty":0}
    for r in session.execute(daily_oil, {"name": attendant_name, "since": since}).fetchall():
        d = str(r[0])
        if d not in daily_data: daily_data[d] = {"date": d, "fuel_txn":0, "fuel_rev":0, "fuel_liters":0, "oil_txn":0, "oil_rev":0, "oil_qty":0}
        daily_data[d]["oil_txn"] = r[1]; daily_data[d]["oil_rev"] = r[2]; daily_data[d]["oil_qty"] = r[3]

    for d in daily_data.values():
        d["total_txn"] = d["fuel_txn"] + d["oil_txn"]
        d["total_rev"] = round((d["fuel_rev"] or 0) + (d["oil_rev"] or 0),2)

    mix_sql = text("""
        SELECT f.name, COUNT(*) as cnt, SUM(s.liters_sold) as liters, SUM(s.total_amount) as rev
        FROM sale s JOIN fuel f ON f.id=s.fuel_id WHERE s.attendant_name=:name AND s.sold_at>=:since GROUP BY f.name
    """)
    fuel_mix = [{"fuel_name": r[0], "transactions": r[1], "liters": round(r[2],2), "revenue": round(r[3],2)} for r in session.execute(mix_sql, {"name": attendant_name, "since": since}).fetchall()]

    hourly_sql = text("""
        SELECT CAST(strftime('%H', sold_at) AS INTEGER) as hour, COUNT(*) as cnt, SUM(total_amount) as rev
        FROM sale WHERE attendant_name=:name AND sold_at>=:since GROUP BY hour
    """)
    hourly = [{"hour": r[0], "transactions": r[1], "revenue": round(r[2],2)} for r in session.execute(hourly_sql, {"name": attendant_name, "since": since}).fetchall()]

    total_rev = (f_rev or 0) + (o_rev or 0)
    total_txn = (f_cnt or 0) + (o_cnt or 0)

    return {
        "attendant_name": attendant_name,
        "period_days": days,
        "summary": {
            "total_revenue": round(total_rev,2),
            "total_transactions": total_txn,
            "fuel": {"transactions": f_cnt, "revenue": round(f_rev or 0,2), "liters": round(f_liters or 0,2), "avg_ticket": round(f_avg or 0,2)},
            "oil": {"transactions": o_cnt, "revenue": round(o_rev or 0,2), "quantity": int(o_qty or 0), "avg_ticket": round(o_avg or 0,2)},
            "overall_avg_ticket": round(total_rev/total_txn,2) if total_txn else 0
        },
        "daily_trend": sorted(daily_data.values(), key=lambda x: x["date"]),
        "fuel_mix": fuel_mix,
        "hourly_pattern": hourly,
        "insight": f"{attendant_name} averaged ₱{round(total_rev/days,2) if days else 0}/day with {round(total_txn/days,1) if days else 0} transactions/day."
    }