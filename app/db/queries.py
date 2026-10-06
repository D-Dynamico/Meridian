"""Read queries behind the API, kept out of the routes so they stay thin.

Summary figures are computed here on every read, never stored (docs/ARCHITECTURE.md §4),
so they cannot drift from the Feature rows.
"""

import uuid
from dataclasses import dataclass

from sqlmodel import Session, func, select

from app.db.models import Feature, MeasurementType

NO_GEOMETRY = "No geometry"


@dataclass(frozen=True)
class Totals:
    by_type: dict[str, int]
    area_m2: float
    length_m: float


def file_totals(session: Session, file_id: uuid.UUID) -> Totals:
    counts = session.exec(
        select(Feature.geometry_type, func.count())
        .where(Feature.file_id == file_id)
        .group_by(Feature.geometry_type)
    ).all()
    sums = dict(
        session.exec(
            select(Feature.measurement_type, func.sum(Feature.value))
            .where(Feature.file_id == file_id, Feature.value.is_not(None))
            .group_by(Feature.measurement_type)
        ).all()
    )
    return Totals(
        by_type={(kind or NO_GEOMETRY): count for kind, count in sorted(counts, key=_type_order)},
        area_m2=sums.get(MeasurementType.AREA) or 0.0,
        length_m=sums.get(MeasurementType.LENGTH) or 0.0,
    )


def measurements_page(
    session: Session, file_id: uuid.UUID, limit: int, offset: int, geometry_type: str | None
) -> tuple[int, list[Feature]]:
    """One page of features in file order, and the total matching the filter."""
    conditions = [Feature.file_id == file_id]
    if geometry_type is not None:
        conditions.append(Feature.geometry_type == geometry_type)
    total = session.exec(select(func.count()).select_from(Feature).where(*conditions)).one()
    rows = session.exec(
        select(Feature).where(*conditions).order_by(Feature.feature_index).offset(offset).limit(limit)
    ).all()
    return total, list(rows)


def _type_order(pair) -> tuple[bool, str]:
    kind = pair[0]
    return (kind is None, kind or "")
