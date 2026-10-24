"""Endpoints shared by both portals: skill dictionary and in-app notifications."""
from fastapi import APIRouter, Depends, Query

from ..auth import CurrentUser, get_current_user
from ..db import user_tx
from ..schemas import Page, paged

router = APIRouter(prefix="/api", tags=["common"])


@router.get("/skills")
async def list_skills(user: CurrentUser = Depends(get_current_user)):
    async with user_tx(user.user_id) as conn:
        rows = await conn.fetch("SELECT skill_id, skill_name, category FROM skills ORDER BY category, skill_name")
    return {"items": [dict(r) for r in rows]}


@router.get("/notifications")
async def list_notifications(unread: bool = Query(False), page: Page = Depends(), user: CurrentUser = Depends(get_current_user)):
    async with user_tx(user.user_id) as conn:
        rows = await conn.fetch(
            """SELECT notification_id, type, title, body, link, is_read, created_at, count(*) OVER () AS total_count
               FROM notifications WHERE ($1::boolean IS FALSE OR NOT is_read)
               ORDER BY created_at DESC LIMIT $2 OFFSET $3""",
            unread, page.limit, page.offset)
        unread_count = await conn.fetchval("SELECT count(*) FROM notifications WHERE NOT is_read")
    return {**paged(rows, page), "unread": unread_count}


@router.post("/notifications/read-all", status_code=204)
async def read_all(user: CurrentUser = Depends(get_current_user)):
    async with user_tx(user.user_id) as conn:
        await conn.execute("UPDATE notifications SET is_read = TRUE WHERE NOT is_read")


@router.post("/notifications/{notification_id}/read", status_code=204)
async def read_one(notification_id: int, user: CurrentUser = Depends(get_current_user)):
    async with user_tx(user.user_id) as conn:
        await conn.execute("UPDATE notifications SET is_read = TRUE WHERE notification_id = $1", notification_id)
