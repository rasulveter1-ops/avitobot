from aiohttp import web
from sqlalchemy import text
from database.connection import AsyncSessionLocal

routes = web.RouteTableDef()


@routes.get("/api/search")
async def api_search(request):
    brand = request.query.get("brand", "").lower()
    model = request.query.get("model", "").lower()
    city = request.query.get("city", "").lower()
    budget = request.query.get("budget", "999999999")

    try:
        budget = int(budget)
    except Exception:
        budget = 999999999

    query = """
        SELECT
            id,
            title,
            price,
            city,
            year,
            mileage,
            url
        FROM catalog
        WHERE price <= :budget
    """

    params = {
        "budget": budget
    }

    if brand:
        query += " AND LOWER(title) LIKE :brand"
        params["brand"] = f"%{brand}%"

    if model:
        query += " AND LOWER(title) LIKE :model"
        params["model"] = f"%{model}%"

    if city:
        query += " AND LOWER(city) LIKE :city"
        params["city"] = f"%{city}%"

    query += """
        ORDER BY price ASC
        LIMIT 20
    """

    async with AsyncSessionLocal() as session:
        result = await session.execute(text(query), params)
        rows = result.fetchall()

    cars = []

    for row in rows:
        cars.append({
            "id": row.id,
            "title": row.title,
            "price": row.price,
            "city": row.city,
            "year": row.year,
            "mileage": row.mileage,
            "url": row.url
        })

    return web.json_response({
        "success": True,
        "count": len(cars),
        "cars": cars
    })


def setup_api(app):
    app.add_routes(routes)