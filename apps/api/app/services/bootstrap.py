import structlog
from sqlalchemy import func, select

from app.config import get_settings
from app.db import session_scope
from app.models import User, UserRole, UserStatus

logger = structlog.get_logger()


async def promote_owner_if_needed() -> None:
    """If no admin exists and OWNER_OPEN_ID matches an existing user, promote them.

    Bootstrap rule: the very first user who logs in via OTP with the email matching
    `OWNER_OPEN_ID` is promoted to admin/active. This runs at startup so the
    promotion is also applied if the user existed before the env was set.
    """
    settings = get_settings()
    owner_open_id = settings.owner_open_id
    if not owner_open_id:
        return

    async with session_scope() as session:
        admin_count = (
            await session.execute(
                select(func.count())
                .select_from(User)
                .where(User.role == UserRole.ADMIN, User.disabled_at.is_(None))
            )
        ).scalar_one()
        if admin_count and int(admin_count) > 0:
            return

        res = await session.execute(select(User).where(User.open_id == owner_open_id))
        owner = res.scalar_one_or_none()
        if owner is None:
            logger.info("bootstrap_owner_pending", open_id=owner_open_id)
            return

        owner.role = UserRole.ADMIN
        owner.status = UserStatus.ACTIVE
        owner.disabled_at = None
        logger.info("bootstrap_owner_promoted", open_id=owner_open_id, id=str(owner.id))
