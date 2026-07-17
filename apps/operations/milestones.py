from dataclasses import dataclass
from enum import StrEnum


PERCENT_SCALE = 100


class MilestoneProgressStatus(StrEnum):
    UNDEFINED = 'undefined'
    IN_PROGRESS = 'in_progress'
    COMPLETED = 'completed'


@dataclass(frozen=True, slots=True)
class MilestoneProgress:
    total: int
    completed: int
    percentage: int | None
    status: MilestoneProgressStatus
    label: str
    is_completed: bool


# PRE: milestones is an iterable already selected by the caller; each item exposes is_completed.
# POST: returns immutable derived progress without mutating items or consulting project state.
def get_milestone_progress(milestones):
    total = 0
    completed = 0
    for milestone in milestones:
        total += 1
        completed += int(bool(milestone.is_completed))

    if total == 0:
        return MilestoneProgress(
            total=0,
            completed=0,
            percentage=None,
            status=MilestoneProgressStatus.UNDEFINED,
            label='Sin hitos definidos',
            is_completed=False,
        )

    percentage = (completed * PERCENT_SCALE + total // 2) // total
    all_completed = completed == total
    status = (
        MilestoneProgressStatus.COMPLETED
        if all_completed
        else MilestoneProgressStatus.IN_PROGRESS
    )
    status_label = 'Completado' if all_completed else 'En progreso'
    return MilestoneProgress(
        total=total,
        completed=completed,
        percentage=percentage,
        status=status,
        label=f'{percentage} % · {status_label}',
        is_completed=all_completed,
    )
