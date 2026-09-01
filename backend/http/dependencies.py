from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from turnstile_core.config import get_settings
from turnstile_core.persistence.factory import create_repository
from turnstile_core.persistence.repository import QueryRepository


@lru_cache
def get_repository() -> QueryRepository:
    return create_repository(get_settings())


Repository = Annotated[QueryRepository, Depends(get_repository)]
