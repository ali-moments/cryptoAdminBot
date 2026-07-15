from typing import Any, Generic, TypeVar

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.base import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    model: type[ModelType]

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs: Any) -> ModelType:
        obj = self.model(**kwargs)
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def add(self, obj: ModelType) -> ModelType:
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def get(self, id: int) -> ModelType | None:
        return await self.session.get(self.model, id)

    async def exists(self, id: int) -> bool:
        stmt = (
            select(func.count())
            .select_from(self.model)
            .where(self.model.id == id)
        )
        return (await self.session.scalar(stmt)) > 0

    async def list(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ModelType]:
        stmt = select(self.model).offset(offset)

        if limit is not None:
            stmt = stmt.limit(limit)

        result = await self.session.scalars(stmt)
        return list(result)

    async def count(self) -> int:
        stmt = select(func.count()).select_from(self.model)
        return (await self.session.scalar(stmt)) or 0

    async def delete(self, obj: ModelType) -> None:
        await self.session.delete(obj)

    async def delete_by_id(self, id: int) -> bool:
        stmt = delete(self.model).where(self.model.id == id)
        result = await self.session.execute(stmt)
        return result.rowcount > 0

    async def flush(self) -> None:
        await self.session.flush()

    async def refresh(self, obj: ModelType) -> None:
        await self.session.refresh(obj)
