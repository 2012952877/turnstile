from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from ..config import get_settings
from ..persistence.factory import create_repository
from ..persistence.repository import QueryRepository


@lru_cache
def get_repository() -> QueryRepository:
    return create_repository(get_settings())


Repository = Annotated[QueryRepository, Depends(get_repository)]
