"""Predictive intelligence: win likelihood and risk for open deals.

The model is learned from the workspace's own closed deals:

* the win rate of deals that reached each stage (smoothed toward the
  stage's configured probability when history is thin),
* adjusted by evidence known to move outcomes: how recently anyone worked
  the deal, whether the close date has slipped, how long it has been open
  compared with typical won deals, the owner's track record, deal size and
  whether a next step is set.

Each prediction lists the factors that raised or lowered it.
"""

from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.opportunity import Opportunity
from app.models.opportunity_stage_history import OpportunityStageHistory
from app.models.pipeline_stage import PipelineStage

PRIOR_WEIGHT = 6.0


@dataclass
class Prediction:
    probability: int
    label: str
    factors: list[dict[str, str]] = field(default_factory=list)
    predicted_close_date: date | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "probability": self.probability,
            "label": self.label,
            "factors": self.factors,
            "predicted_close_date": self.predicted_close_date,
        }


@dataclass
class Model:
    stage_rates: dict[uuid.UUID, float]
    org_win_rate: float
    owner_rates: dict[uuid.UUID, float]
    median_cycle_days: float
    median_won_amount: float
    closed_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "closed_deals": self.closed_count,
            "win_rate": round(self.org_win_rate * 100, 1),
            "median_cycle_days": round(self.median_cycle_days),
            "median_won_amount": round(self.median_won_amount, 2),
            "confidence": "high" if self.closed_count >= 50 else "medium" if self.closed_count >= 15 else "low",
        }


def _today() -> date:
    return datetime.now(timezone.utc).date()


async def train(db: AsyncSession, organization_id: uuid.UUID) -> Model:
    stages = (
        await db.execute(select(PipelineStage).where(PipelineStage.organization_id == organization_id))
    ).scalars().all()
    category = {stage.id: stage.category for stage in stages}
    prior = {stage.id: max(0.02, min(0.98, (stage.probability or 0) / 100)) for stage in stages}

    closed = (
        await db.execute(
            select(Opportunity).where(
                Opportunity.organization_id == organization_id,
                Opportunity.stage_id.in_([s.id for s in stages if s.category in {"won", "lost"}] or [uuid.uuid4()]),
            )
        )
    ).scalars().all()
    closed_ids = [opp.id for opp in closed]
    reached: dict[uuid.UUID, set[uuid.UUID]] = {opp.id: {opp.stage_id} for opp in closed}
    if closed_ids:
        for opportunity_id, stage_id in (
            await db.execute(
                select(OpportunityStageHistory.opportunity_id, OpportunityStageHistory.to_stage_id).where(
                    OpportunityStageHistory.opportunity_id.in_(closed_ids)
                )
            )
        ).all():
            reached.setdefault(opportunity_id, set()).add(stage_id)

    won_ids = {opp.id for opp in closed if category.get(opp.stage_id) == "won"}
    totals: dict[uuid.UUID, list[int]] = {}
    for opportunity_id, stage_ids in reached.items():
        for stage_id in stage_ids:
            if category.get(stage_id) != "open":
                continue
            bucket = totals.setdefault(stage_id, [0, 0])
            bucket[1] += 1
            if opportunity_id in won_ids:
                bucket[0] += 1
    stage_rates = {}
    for stage in stages:
        if stage.category != "open":
            continue
        wins, count = totals.get(stage.id, [0, 0])
        stage_rates[stage.id] = (wins + PRIOR_WEIGHT * prior[stage.id]) / (count + PRIOR_WEIGHT)

    org_win_rate = (len(won_ids) + 1) / (len(closed) + 2)

    owner_counts: dict[uuid.UUID, list[int]] = {}
    for opp in closed:
        if opp.owner_user_id is None:
            continue
        bucket = owner_counts.setdefault(opp.owner_user_id, [0, 0])
        bucket[1] += 1
        if opp.id in won_ids:
            bucket[0] += 1
    owner_rates = {
        owner: (wins + 4 * org_win_rate) / (count + 4) for owner, (wins, count) in owner_counts.items()
    }

    won = [opp for opp in closed if opp.id in won_ids]
    cycles = [
        max(1, ((opp.closed_at or opp.updated_at) - opp.created_at).days)
        for opp in won
        if opp.created_at and (opp.closed_at or opp.updated_at)
    ]
    amounts = [float(opp.amount) for opp in won if opp.amount]
    return Model(
        stage_rates=stage_rates,
        org_win_rate=org_win_rate,
        owner_rates=owner_rates,
        median_cycle_days=statistics.median(cycles) if cycles else 45,
        median_won_amount=statistics.median(amounts) if amounts else 0,
        closed_count=len(closed),
    )


def _odds(p: float) -> float:
    p = max(0.01, min(0.99, p))
    return p / (1 - p)


def predict(model: Model, opp: Opportunity, last_activity: datetime | None, stage_probability: int | None = None) -> Prediction:
    base = model.stage_rates.get(opp.stage_id)
    if base is None:
        base = max(0.02, min(0.98, (stage_probability or opp.probability or 20) / 100))
    odds = _odds(base)
    factors: list[dict[str, str]] = [
        {"label": f"Deals that reach this stage win {round(base * 100)}% of the time", "impact": "neutral"}
    ]
    today = _today()

    def adjust(multiplier: float, label: str) -> None:
        nonlocal odds
        odds *= multiplier
        factors.append({"label": label, "impact": "positive" if multiplier > 1 else "negative"})

    if last_activity is None:
        adjust(0.55, "No calls, emails or meetings logged")
    else:
        idle = (datetime.now(timezone.utc) - last_activity).days
        if idle <= 7:
            adjust(1.15, f"Worked {idle} day{'s' if idle != 1 else ''} ago")
        elif idle > 45:
            adjust(0.55, f"No activity for {idle} days")
        elif idle > 21:
            adjust(0.75, f"No activity for {idle} days")

    if opp.expected_close_date:
        if opp.expected_close_date < today:
            adjust(0.6, f"Close date passed {(today - opp.expected_close_date).days} days ago")
        elif (opp.expected_close_date - today).days <= 30:
            adjust(1.1, "Closing within 30 days")
    else:
        adjust(0.85, "No close date")

    if opp.created_at:
        age = (datetime.now(timezone.utc) - opp.created_at).days
        if model.median_cycle_days and age > 2 * model.median_cycle_days:
            adjust(0.7, f"Open {age} days, over twice the usual {round(model.median_cycle_days)}")

    if opp.owner_user_id in model.owner_rates and model.closed_count >= 10:
        ratio = model.owner_rates[opp.owner_user_id] / max(0.01, model.org_win_rate)
        multiplier = max(0.75, min(1.3, ratio ** 0.5))
        if multiplier >= 1.07:
            adjust(multiplier, "Owner wins more often than the team")
        elif multiplier <= 0.93:
            adjust(multiplier, "Owner wins less often than the team")

    if opp.amount and model.median_won_amount and float(opp.amount) > 3 * model.median_won_amount:
        adjust(0.85, "Much larger than the typical won deal")

    if opp.next_step:
        adjust(1.05, "Next step is set")
    else:
        adjust(0.9, "No next step")

    probability = round(odds / (1 + odds) * 100)
    probability = max(2, min(98, probability))
    label = "likely" if probability >= 65 else "possible" if probability >= 35 else "at_risk"
    remaining = max(7, round(model.median_cycle_days - ((datetime.now(timezone.utc) - opp.created_at).days if opp.created_at else 0)))
    if opp.expected_close_date and opp.expected_close_date >= today:
        predicted_close = opp.expected_close_date
    else:
        predicted_close = today + timedelta(days=remaining)
    return Prediction(probability, label, factors, predicted_close)


async def last_activities(db: AsyncSession, organization_id: uuid.UUID, opps: list[Opportunity]) -> dict[uuid.UUID, datetime]:
    if not opps:
        return {}
    by_opp = dict(
        (
            await db.execute(
                select(Activity.opportunity_id, func.max(Activity.occurred_at))
                .where(Activity.organization_id == organization_id, Activity.opportunity_id.in_([o.id for o in opps]))
                .group_by(Activity.opportunity_id)
            )
        ).all()
    )
    by_account = dict(
        (
            await db.execute(
                select(Activity.account_id, func.max(Activity.occurred_at))
                .where(Activity.organization_id == organization_id, Activity.account_id.in_({o.account_id for o in opps}))
                .group_by(Activity.account_id)
            )
        ).all()
    )
    result = {}
    for opp in opps:
        candidates = [value for value in (by_opp.get(opp.id), by_account.get(opp.account_id)) if value]
        if candidates:
            result[opp.id] = max(candidates)
    return result


async def predict_open(db: AsyncSession, organization_id: uuid.UUID, owner_id: uuid.UUID | None = None) -> tuple[Model, list[tuple[Opportunity, Prediction]]]:
    model = await train(db, organization_id)
    stages = {
        stage.id: stage
        for stage in (await db.execute(select(PipelineStage).where(PipelineStage.organization_id == organization_id))).scalars()
    }
    open_ids = [stage_id for stage_id, stage in stages.items() if stage.category == "open"]
    query = select(Opportunity).where(Opportunity.organization_id == organization_id, Opportunity.stage_id.in_(open_ids or [uuid.uuid4()]))
    if owner_id is not None:
        query = query.where(Opportunity.owner_user_id == owner_id)
    opps = list((await db.execute(query)).scalars().all())
    activity = await last_activities(db, organization_id, opps)
    return model, [
        (opp, predict(model, opp, activity.get(opp.id), stages[opp.stage_id].probability if opp.stage_id in stages else None))
        for opp in opps
    ]
