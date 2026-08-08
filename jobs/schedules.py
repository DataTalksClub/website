from django_q.models import Schedule  # type: ignore[import-untyped]

from jobs.registry import ScheduleDefinition, register_schedule

register_schedule(
    ScheduleDefinition(
        key="dtc:durable-job-relay",
        func="jobs.tasks.sweep_and_relay",
        schedule_type=Schedule.MINUTES,
        minutes=1,
        repeats=-1,
    )
)
