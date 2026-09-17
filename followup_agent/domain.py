"""Domain types and strict input parsing for fictional follow-up records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Mapping


class InputValidationError(ValueError):
    """Raised when input cannot be evaluated safely."""


class ReasonCode(str, Enum):
    OVERDUE_AND_CONTACT_ELIGIBLE = "OVERDUE_AND_CONTACT_ELIGIBLE"
    NOT_OVERDUE = "NOT_OVERDUE"
    FOLLOW_UP_REQUIREMENT_NOT_OPEN = "FOLLOW_UP_REQUIREMENT_NOT_OPEN"
    COVERING_APPOINTMENT_EXISTS = "COVERING_APPOINTMENT_EXISTS"
    MESSAGING_CONSENT_NOT_CURRENT = "MESSAGING_CONSENT_NOT_CURRENT"
    CONTACT_DETAILS_INVALID = "CONTACT_DETAILS_INVALID"
    CONTACT_TIMING_NOT_PERMITTED = "CONTACT_TIMING_NOT_PERMITTED"
    CONTACT_ATTEMPT_LIMIT_REACHED = "CONTACT_ATTEMPT_LIMIT_REACHED"
    PENDING_APPROVAL_EXISTS = "PENDING_APPROVAL_EXISTS"
    UNRESOLVED_ESCALATION_EXISTS = "UNRESOLVED_ESCALATION_EXISTS"
    SOURCE_RECORD_STALE = "SOURCE_RECORD_STALE"
    CLINICAL_INSTRUCTION_REVIEW_REQUIRED = (
        "CLINICAL_INSTRUCTION_REVIEW_REQUIRED"
    )


@dataclass(frozen=True)
class Consent:
    channel: str
    granted: bool
    expires_at: datetime | None


@dataclass(frozen=True)
class ContactDetails:
    channel: str
    is_valid: bool


@dataclass(frozen=True)
class Patient:
    patient_id: str
    consent: Consent
    contact: ContactDetails


@dataclass(frozen=True)
class TreatmentEpisode:
    episode_id: str
    clinical_status: str
    source_record_version: int


@dataclass(frozen=True)
class FollowUpRequirement:
    follow_up_id: str
    episode_id: str
    state: str
    approved_due_date: date
    source_record_version: int
    source_records_current: bool
    instructions_consistent: bool


@dataclass(frozen=True)
class Appointment:
    appointment_id: str
    starts_at: datetime
    status: str
    covered_follow_up_ids: frozenset[str]


@dataclass(frozen=True)
class FollowUpTask:
    contact_attempts: int
    next_permitted_contact_at: datetime | None
    has_pending_approval: bool
    has_unresolved_escalation: bool


@dataclass(frozen=True)
class EligibilityPolicy:
    clinic_timezone: str
    channel: str
    max_contact_attempts: int
    sending_window_start_hour: int | None = None
    sending_window_end_hour: int | None = None


@dataclass(frozen=True)
class EligibilityCase:
    case_id: str
    evaluated_at: datetime
    patient: Patient
    episode: TreatmentEpisode
    requirement: FollowUpRequirement
    appointments: tuple[Appointment, ...]
    task: FollowUpTask
    policy: EligibilityPolicy


@dataclass(frozen=True)
class EligibilityResult:
    case_id: str
    patient_id: str
    follow_up_id: str
    evaluated_at: datetime
    clinic_date: date
    is_overdue: bool
    may_send_reminder: bool
    reason_codes: tuple[ReasonCode, ...]
    source_versions: Mapping[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "patient_id": self.patient_id,
            "follow_up_id": self.follow_up_id,
            "evaluated_at": self.evaluated_at.isoformat(),
            "clinic_date": self.clinic_date.isoformat(),
            "is_overdue": self.is_overdue,
            "may_send_reminder": self.may_send_reminder,
            "reason_codes": [code.value for code in self.reason_codes],
            "source_versions": dict(self.source_versions),
        }


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InputValidationError(f"{field} must be an object")
    return value


def _require_string(data: Mapping[str, Any], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise InputValidationError(f"{field} must be a non-empty string")
    return value


def _require_bool(data: Mapping[str, Any], field: str) -> bool:
    value = data.get(field)
    if not isinstance(value, bool):
        raise InputValidationError(f"{field} must be a boolean")
    return value


def _require_int(data: Mapping[str, Any], field: str, minimum: int = 0) -> int:
    value = data.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise InputValidationError(f"{field} must be an integer >= {minimum}")
    return value


def _parse_datetime(value: Any, field: str, optional: bool = False) -> datetime | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise InputValidationError(f"{field} must be an ISO-8601 datetime")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise InputValidationError(f"{field} must be an ISO-8601 datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InputValidationError(f"{field} must include a timezone offset")
    return parsed


def _parse_date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise InputValidationError(f"{field} must be an ISO-8601 date")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise InputValidationError(f"{field} must be an ISO-8601 date") from error


def _parse_hour(data: Mapping[str, Any], field: str) -> int | None:
    value = data.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 23:
        raise InputValidationError(f"{field} must be an integer from 0 to 23")
    return value


def parse_case(data: Mapping[str, Any], defaults: Mapping[str, Any]) -> EligibilityCase:
    """Parse and validate a case without inferring missing clinical facts."""

    case = _require_mapping(data, "case")
    patient_data = _require_mapping(case.get("patient"), "patient")
    consent_data = _require_mapping(patient_data.get("consent"), "patient.consent")
    contact_data = _require_mapping(patient_data.get("contact"), "patient.contact")
    requirement_data = _require_mapping(case.get("requirement"), "requirement")
    episode_data = _require_mapping(case.get("episode"), "episode")
    task_data = _require_mapping(case.get("task"), "task")
    policy_data = dict(defaults)
    policy_override = case.get("policy", {})
    policy_data.update(_require_mapping(policy_override, "policy"))

    consent = Consent(
        channel=_require_string(consent_data, "channel").lower(),
        granted=_require_bool(consent_data, "granted"),
        expires_at=_parse_datetime(
            consent_data.get("expires_at"), "consent.expires_at", optional=True
        ),
    )
    contact = ContactDetails(
        channel=_require_string(contact_data, "channel").lower(),
        is_valid=_require_bool(contact_data, "is_valid"),
    )
    patient = Patient(
        patient_id=_require_string(patient_data, "patient_id"),
        consent=consent,
        contact=contact,
    )
    episode = TreatmentEpisode(
        episode_id=_require_string(episode_data, "episode_id"),
        clinical_status=_require_string(episode_data, "clinical_status"),
        source_record_version=_require_int(
            episode_data, "source_record_version", minimum=1
        ),
    )
    requirement = FollowUpRequirement(
        follow_up_id=_require_string(requirement_data, "follow_up_id"),
        episode_id=_require_string(requirement_data, "episode_id"),
        state=_require_string(requirement_data, "state").upper(),
        approved_due_date=_parse_date(
            requirement_data.get("approved_due_date"),
            "requirement.approved_due_date",
        ),
        source_record_version=_require_int(
            requirement_data, "source_record_version", minimum=1
        ),
        source_records_current=_require_bool(
            requirement_data, "source_records_current"
        ),
        instructions_consistent=_require_bool(
            requirement_data, "instructions_consistent"
        ),
    )
    if requirement.episode_id != episode.episode_id:
        raise InputValidationError(
            "requirement.episode_id must match the supplied treatment episode"
        )

    appointment_items = case.get("appointments", [])
    if not isinstance(appointment_items, list):
        raise InputValidationError("appointments must be an array")
    appointments = tuple(_parse_appointment(item) for item in appointment_items)

    task = FollowUpTask(
        contact_attempts=_require_int(task_data, "contact_attempts"),
        next_permitted_contact_at=_parse_datetime(
            task_data.get("next_permitted_contact_at"),
            "task.next_permitted_contact_at",
            optional=True,
        ),
        has_pending_approval=_require_bool(task_data, "has_pending_approval"),
        has_unresolved_escalation=_require_bool(
            task_data, "has_unresolved_escalation"
        ),
    )

    policy = EligibilityPolicy(
        clinic_timezone=_require_string(policy_data, "clinic_timezone"),
        channel=_require_string(policy_data, "channel").lower(),
        max_contact_attempts=_require_int(
            policy_data, "max_contact_attempts", minimum=1
        ),
        sending_window_start_hour=_parse_hour(
            policy_data, "sending_window_start_hour"
        ),
        sending_window_end_hour=_parse_hour(policy_data, "sending_window_end_hour"),
    )

    if (policy.sending_window_start_hour is None) != (
        policy.sending_window_end_hour is None
    ):
        raise InputValidationError(
            "sending window start and end hours must both be set or both be null"
        )
    if (
        policy.sending_window_start_hour is not None
        and policy.sending_window_start_hour == policy.sending_window_end_hour
    ):
        raise InputValidationError("sending window start and end cannot be equal")

    evaluated_at = _parse_datetime(case.get("evaluated_at"), "evaluated_at")
    assert evaluated_at is not None
    return EligibilityCase(
        case_id=_require_string(case, "case_id"),
        evaluated_at=evaluated_at,
        patient=patient,
        episode=episode,
        requirement=requirement,
        appointments=appointments,
        task=task,
        policy=policy,
    )


def parse_cases(document: Mapping[str, Any]) -> tuple[EligibilityCase, ...]:
    root = _require_mapping(document, "document")
    defaults = _require_mapping(root.get("policy_defaults"), "policy_defaults")
    cases = root.get("cases")
    if not isinstance(cases, list) or not cases:
        raise InputValidationError("cases must be a non-empty array")
    parsed = tuple(parse_case(item, defaults) for item in cases)
    ids = [case.case_id for case in parsed]
    if len(ids) != len(set(ids)):
        raise InputValidationError("case_id values must be unique")
    return parsed


def _parse_appointment(data: Any) -> Appointment:
    item = _require_mapping(data, "appointment")
    covered = item.get("covered_follow_up_ids")
    if not isinstance(covered, list) or any(
        not isinstance(value, str) or not value for value in covered
    ):
        raise InputValidationError(
            "appointment.covered_follow_up_ids must be an array of identifiers"
        )
    starts_at = _parse_datetime(item.get("starts_at"), "appointment.starts_at")
    assert starts_at is not None
    return Appointment(
        appointment_id=_require_string(item, "appointment_id"),
        starts_at=starts_at,
        status=_require_string(item, "status").upper(),
        covered_follow_up_ids=frozenset(covered),
    )
