import os
from aiohttp import web
from loguru import logger
from webapp.api import setup_api


async def index(request):
    file_path = os.path.join(os.path.dirname(__file__), "index.html")
    return web.FileResponse(file_path)


async def start_webapp():
    app = web.Application()

    app.router.add_get("/", index)

    setup_api(app)

    port = int(os.getenv("PORT", 8080))

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    logger.info(f"🌐 WebApp запущен на порту {port}")