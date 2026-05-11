from aiohttp import web
from sqlalchemy import text
from database.connection import AsyncSessionLocal

routes = web.RouteTableDef()


def safe_int(value, default=0):

    try:

        if value is None:
            return default

        return int(value)

    except Exception:
        return default


def calc_deal_score(
    price,
    avg_price_rf,
    avg_price_region,
    mileage,
    year
):

    price = safe_int(price)
    avg_price_rf = safe_int(avg_price_rf)
    avg_price_region = safe_int(avg_price_region)
    mileage = safe_int(mileage)
    year = safe_int(year)

    score = 50

    base_avg = avg_price_region or avg_price_rf

    if base_avg > 0 and price > 0:

        discount_percent = (
            (base_avg - price) / base_avg
        ) * 100

        if discount_percent >= 30:
            score += 35

        elif discount_percent >= 20:
            score += 28

        elif discount_percent >= 15:
            score += 22

        elif discount_percent >= 10:
            score += 15

        elif discount_percent >= 5:
            score += 8

    if year >= 2020:
        score += 8

    elif year >= 2017:
        score += 5

    elif year >= 2014:
        score += 2

    if mileage > 0:

        if mileage <= 50000:
            score += 7

        elif mileage <= 100000:
            score += 4

        elif mileage <= 150000:
            score += 2

        elif mileage >= 300000:
            score -= 8

    if score > 99:
        score = 99

    if score < 1:
        score = 1

    return int(score)


@routes.get("/api/search")
async def api_search(request):

    brand = request.query.get(
        "brand",
        ""
    ).lower().strip()

    model = request.query.get(
        "model",
        ""
    ).lower().strip()

    city = request.query.get(
        "city",
        ""
    ).lower().strip()

    budget = request.query.get(
        "budget",
        "999999999"
    )

    only_deals = request.query.get(
        "only_deals",
        "0"
    )

    budget = safe_int(
        budget,
        999999999
    )

    where_sql = """
        WHERE price > 0
        AND price <= :budget
    """

    params = {
        "budget": budget
    }

    if brand:

        where_sql += """
            AND LOWER(title)
            LIKE :brand
        """

        params["brand"] = f"%{brand}%"

    if model:

        where_sql += """
            AND LOWER(title)
            LIKE :model
        """

        params["model"] = f"%{model}%"

    if city:

        where_sql += """
            AND LOWER(city)
            LIKE :city
        """

        params["city"] = f"%{city}%"

    stats_sql = text(f"""

        SELECT

            AVG(price)::BIGINT
                AS avg_price_rf,

            MIN(price)::BIGINT
                AS min_price_rf,

            MAX(price)::BIGINT
                AS max_price_rf,

            COUNT(*)::BIGINT
                AS total_count

        FROM catalog

        {where_sql}

    """)

    region_stats_sql = text(f"""

        SELECT

            city,

            AVG(price)::BIGINT
                AS avg_price_region,

            MIN(price)::BIGINT
                AS min_price_region,

            MAX(price)::BIGINT
                AS max_price_region,

            COUNT(*)::BIGINT
                AS region_count

        FROM catalog

        {where_sql}

        GROUP BY city

    """)

    cars_sql = text(f"""

        SELECT

            id,
            title,
            price,
            city,
            year,
            mileage,
            url,
            image_url

        FROM catalog

        {where_sql}

        ORDER BY price ASC

        LIMIT 80

    """)

    async with AsyncSessionLocal() as session:

        stats_result = await session.execute(
            stats_sql,
            params
        )

        stats = (
            stats_result
            .mappings()
            .first()
        )

        region_result = await session.execute(
            region_stats_sql,
            params
        )

        region_rows = (
            region_result
            .mappings()
            .all()
        )

        cars_result = await session.execute(
            cars_sql,
            params
        )

        rows = (
            cars_result
            .mappings()
            .all()
        )

    avg_price_rf = safe_int(
        stats.get("avg_price_rf")
        if stats else 0
    )

    min_price_rf = safe_int(
        stats.get("min_price_rf")
        if stats else 0
    )

    max_price_rf = safe_int(
        stats.get("max_price_rf")
        if stats else 0
    )

    total_count = safe_int(
        stats.get("total_count")
        if stats else 0
    )

    region_map = {}

    for r in region_rows:

        region_name = str(
            r.get("city") or ""
        )

        region_map[region_name] = {

            "avg_price_region":
                safe_int(
                    r.get("avg_price_region")
                ),

            "min_price_region":
                safe_int(
                    r.get("min_price_region")
                ),

            "max_price_region":
                safe_int(
                    r.get("max_price_region")
                ),

            "region_count":
                safe_int(
                    r.get("region_count")
                )
        }

    cars = []

    for row in rows:

        price = safe_int(
            row.get("price")
        )

        car_city = str(
            row.get("city") or ""
        )

        region_data = region_map.get(
            car_city,
            {}
        )

        avg_price_region = safe_int(
            region_data.get(
                "avg_price_region"
            )
        )

        min_price_region = safe_int(
            region_data.get(
                "min_price_region"
            )
        )

        max_price_region = safe_int(
            region_data.get(
                "max_price_region"
            )
        )

        region_count = safe_int(
            region_data.get(
                "region_count"
            )
        )

        market_base = (
            avg_price_region
            or avg_price_rf
        )

        below_market_percent = 0
        resale_potential = 0

        if market_base > 0 and price > 0:

            below_market_percent = round(
                (
                    (
                        market_base - price
                    ) / market_base
                ) * 100,
                1
            )

            resale_potential = max(
                market_base - price,
                0
            )

        deal_score = calc_deal_score(
            price=price,
            avg_price_rf=avg_price_rf,
            avg_price_region=avg_price_region,
            mileage=row.get("mileage"),
            year=row.get("year")
        )

        car = {

            "id":
                row.get("id"),

            "title":
                row.get("title"),

            "price":
                price,

            "city":
                car_city,

            "year":
                row.get("year"),

            "mileage":
                row.get("mileage"),

            "url":
                row.get("url"),

            "image_url":
                row.get("image_url"),

            "avg_price_rf":
                avg_price_rf,

            "min_price_rf":
                min_price_rf,

            "max_price_rf":
                max_price_rf,

            "avg_price_region":
                avg_price_region,

            "min_price_region":
                min_price_region,

            "max_price_region":
                max_price_region,

            "region_count":
                region_count,

            "below_market_percent":
                below_market_percent,

            "resale_potential":
                resale_potential,

            "deal_score":
                deal_score
        }

        if only_deals == "1":

            if (
                below_market_percent >= 10
                and resale_potential > 0
            ):
                cars.append(car)

        else:
            cars.append(car)

    cars = sorted(

    cars,

    key=lambda x: (

        0 if x.get("image_url") else 1,

        -x.get("deal_score", 0),

        -x.get("resale_potential", 0)

    )

)

    cars = cars[:20]

    return web.json_response({

        "success": True,

        "count": len(cars),

        "stats": {

            "avg_price_rf":
                avg_price_rf,

            "min_price_rf":
                min_price_rf,

            "max_price_rf":
                max_price_rf,

            "total_count":
                total_count
        },

        "cars":
            cars
    })


@routes.get("/api/top-deals")
async def api_top_deals(request):

    brand = request.query.get(
        "brand",
        ""
    ).lower().strip()

    model = request.query.get(
        "model",
        ""
    ).lower().strip()

    query = """
        /api/search
        ?budget=999999999
        &only_deals=1
    """

    if brand:
        query += f"&brand={brand}"

    if model:
        query += f"&model={model}"

    request = request.clone(
        rel_url=query
    )

    return await api_search(request)


def setup_api(app):

    app.add_routes(routes)