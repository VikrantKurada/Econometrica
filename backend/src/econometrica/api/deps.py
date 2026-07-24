"""Dependencies shared by every router."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from econometrica.db.session import get_session

# Declared as an ``Annotated`` alias rather than a ``Depends()`` default so that
# route signatures stay free of mutable-looking defaults and the tests can
# override ``get_session`` in one place.
SessionDep = Annotated[AsyncSession, Depends(get_session)]
