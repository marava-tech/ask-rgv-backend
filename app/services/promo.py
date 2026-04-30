import secrets
import logging
from db.pool import get_pool

logger = logging.getLogger(__name__)

_MAX_RETRIES = 5


async def generate_app_code() -> str:
    pool = get_pool()
    for _ in range(_MAX_RETRIES):
        code = f"RGVAPP-{secrets.token_hex(4).upper()}"
        existing = await pool.fetchval(
            "SELECT 1 FROM waitlist_signups WHERE app_promo_code = $1", code
        )
        if not existing:
            return code
    raise RuntimeError("promo code collision after max retries")


async def generate_merch_code() -> str:
    pool = get_pool()
    for _ in range(_MAX_RETRIES):
        code = f"RGVMERCH-{secrets.token_hex(4).upper()}"
        existing = await pool.fetchval(
            "SELECT 1 FROM waitlist_signups WHERE merch_promo_code = $1", code
        )
        if not existing:
            return code
    raise RuntimeError("merch promo code collision after max retries")
