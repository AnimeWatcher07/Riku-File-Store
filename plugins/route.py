"""
🌐 ROUTE - chhota web server (Render ko port chahiye + UptimeRobot / self-ping ke liye).
"""
from aiohttp import web

import config
from database.database import db

routes = web.RouteTableDef()


@routes.get("/", allow_head=True)
async def root_route_handler(request):
    brand = db.get_brand()
    return web.json_response({
        "status": "online",
        "service": brand["name"] if brand.get("name") else "Pro Link Protection & Sharing Bot",
        "engine": "SQLite + Telegram channel backup",
    })


@routes.get("/ping", allow_head=True)
@routes.get("/health", allow_head=True)
async def health_check_handler(request):
    return web.Response(text="OK", status=200)


async def web_runner():
    app = web.Application(client_max_size=1024 * 1024)
    app.add_routes(routes)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", config.PORT).start()
    print(f"web port {config.PORT} ready")
    return runner
