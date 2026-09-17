"""Deterministic, side-effect-free follow-up eligibility rules."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .domain import EligibilityCase, EligibilityResult, InputValidationError, ReasonCode


def _consent_is_current(case: EligibilityCase) -> bool:
    consent = case.patient.consent
    if consent.channel != case.policy.channel or not consent.granted:
        return False
    return consent.expires_at is None or consent.expires_at > case.evaluated_at


def _contact_is_valid(case: EligibilityCase) -> bool:
    contact = case.patient.contact
    return contact.channel == case.policy.channel and contact.is_valid


def _inside_sending_window(local_now: datetime, case: EligibilityCase) -> bool:
    start_hour = case.policy.sending_window_start_hour
    end_hour = case.policy.sending_window_end_hour
    if start_hour is None or end_hour is None:
        return True
    current = local_now.timetz().replace(tzinfo=None)
    start = time(hour=start_hour)
    end = time(hour=end_hour)
    if start < end:
        return start <= current < end
    return current >= start or current < end


def _has_covering_appointment(case: EligibilityCase) -> bool:
    follow_up_id = case.requirement.follow_up_id
    return any(
        appointment.status == "CONFIRMED"
        and appointment.starts_at > case.evaluated_at
        and follow_up_id in appointment.covered_follow_up_ids
        for appointment in case.appointments
    )


def evaluate_case(case: EligibilityCase) -> EligibilityResult:
    """Evaluate one requirement and return all applicable deterministic reasons."""

    try:
        clinic_zone = ZoneInfo(case.policy.clinic_timezone)
    except ZoneInfoNotFoundError as error:
        raise InputValidationError(
            f"unknown clinic timezone: {case.policy.clinic_timezone}"
        ) from error

    local_now = case.evaluated_at.astimezone(clinic_zone)
    clinic_today = local_now.date()
    requirement_open = case.requirement.state == "OPEN"
    is_overdue = (
        requirement_open
        and case.requirement.approved_due_date < clinic_today
    )
    reasons: list[ReasonCode] = []

    if not requirement_open:
        reasons.append(ReasonCode.FOLLOW_UP_REQUIREMENT_NOT_OPEN)
    elif not is_overdue:
        reasons.append(ReasonCode.NOT_OVERDUE)

    if _has_covering_appointment(case):
        reasons.append(ReasonCode.COVERING_APPOINTMENT_EXISTS)
    if not _consent_is_current(case):
        reasons.append(ReasonCode.MESSAGING_CONSENT_NOT_CURRENT)
    if not _contact_is_valid(case):
        reasons.append(ReasonCode.CONTACT_DETAILS_INVALID)

    cooldown_active = (
        case.task.next_permitted_contact_at is not None
        and case.task.next_permitted_contact_at > case.evaluated_at
    )
    if cooldown_active or not _inside_sending_window(local_now, case):
        reasons.append(ReasonCode.CONTACT_TIMING_NOT_PERMITTED)
    if case.task.contact_attempts >= case.policy.max_contact_attempts:
        reasons.append(ReasonCode.CONTACT_ATTEMPT_LIMIT_REACHED)
    if case.task.has_pending_approval:
        reasons.append(ReasonCode.PENDING_APPROVAL_EXISTS)
    if case.task.has_unresolved_escalation:
        reasons.append(ReasonCode.UNRESOLVED_ESCALATION_EXISTS)
    if not case.requirement.source_records_current:
        reasons.append(ReasonCode.SOURCE_RECORD_STALE)
    if not case.requirement.instructions_consistent:
        reasons.append(ReasonCode.CLINICAL_INSTRUCTION_REVIEW_REQUIRED)

    may_send = is_overdue and not reasons
    if may_send:
        reasons.append(ReasonCode.OVERDUE_AND_CONTACT_ELIGIBLE)

    return EligibilityResult(
        case_id=case.case_id,
        patient_id=case.patient.patient_id,
        follow_up_id=case.requirement.follow_up_id,
        evaluated_at=case.evaluated_at,
        clinic_date=clinic_today,
        is_overdue=is_overdue,
        may_send_reminder=may_send,
        reason_codes=tuple(reasons),
        source_versions={
            "follow_up": case.requirement.source_record_version,
            "treatment_episode": case.episode.source_record_version,
        },
    )
