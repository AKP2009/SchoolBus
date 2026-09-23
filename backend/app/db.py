from functools import lru_cache

from supabase import Client, create_client

from app.core.config import get_settings


@lru_cache
def get_supabase() -> Client:
    """Supabase client with the service-role key. Bypasses RLS: backend use only."""
    settings = get_settings()
    return create_client(
        settings.supabase_url, settings.supabase_service_role_key.get_secret_value()
    )
