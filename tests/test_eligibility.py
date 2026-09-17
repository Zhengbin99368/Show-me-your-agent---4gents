from __future__ import annotations

import copy
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import mock_open, patch

from followup_agent.cli import run
from followup_agent.domain import InputValidationError, ReasonCode, parse_case, parse_cases
from followup_agent.eligibility import evaluate_case


BASE_CASE = {
    "case_id": "base",
    "evaluated_at": "2030-01-15T09:00:00+08:00",
    "patient": {
        "patient_id": "P-DEMO",
        "consent": {"channel": "sms", "granted": True, "expires_at": None},
        "contact": {"channel": "sms", "is_valid": True},
    },
    "episode": {
        "episode_id": "E-DEMO",
        "clinical_status": "CLINICIAN_RECORDED_OPEN",
        "source_record_version": 1,
    },
    "requirement": {
        "follow_up_id": "F-DEMO",
        "episode_id": "E-DEMO",
        "state": "OPEN",
        "approved_due_date": "2030-01-14",
        "source_record_version": 1,
        "source_records_current": True,
        "instructions_consistent": True,
    },
    "appointments": [],
    "task": {
        "contact_attempts": 0,
        "next_permitted_contact_at": None,
        "has_pending_approval": False,
        "has_unresolved_escalation": False,
    },
}

POLICY = {
    "clinic_timezone": "Asia/Singapore",
    "channel": "sms",
    "max_contact_attempts": 3,
    "sending_window_start_hour": 8,
    "sending_window_end_hour": 20,
}


def evaluate_with(mutator=None):
    data = copy.deepcopy(BASE_CASE)
    if mutator:
        mutator(data)
    return evaluate_case(parse_case(data, POLICY))


class EligibilityTests(unittest.TestCase):
    def test_overdue_eligible(self):
        result = evaluate_with()
        self.assertTrue(result.is_overdue)
        self.assertTrue(result.may_send_reminder)
        self.assertEqual(
            result.reason_codes, (ReasonCode.OVERDUE_AND_CONTACT_ELIGIBLE,)
        )

    def test_due_today_is_not_overdue(self):
        result = evaluate_with(
            lambda data: data["requirement"].update(
                approved_due_date="2030-01-15"
            )
        )
        self.assertFalse(result.is_overdue)
        self.assertFalse(result.may_send_reminder)
        self.assertIn(ReasonCode.NOT_OVERDUE, result.reason_codes)

    def test_clinic_timezone_controls_date(self):
        data = copy.deepcopy(BASE_CASE)
        data["evaluated_at"] = "2030-01-14T16:30:00+00:00"
        data["requirement"]["approved_due_date"] = "2030-01-14"
        result = evaluate_case(parse_case(data, POLICY))
        self.assertEqual(result.clinic_date.isoformat(), "2030-01-15")
        self.assertTrue(result.is_overdue)

    def test_covering_future_appointment_blocks(self):
        def mutate(data):
            data["appointments"] = [
                {
                    "appointment_id": "A1",
                    "starts_at": "2030-01-20T10:00:00+08:00",
                    "status": "CONFIRMED",
                    "covered_follow_up_ids": ["F-DEMO"],
                }
            ]

        result = evaluate_with(mutate)
        self.assertIn(ReasonCode.COVERING_APPOINTMENT_EXISTS, result.reason_codes)
        self.assertFalse(result.may_send_reminder)

    def test_unrelated_appointment_does_not_block(self):
        def mutate(data):
            data["appointments"] = [
                {
                    "appointment_id": "A1",
                    "starts_at": "2030-01-20T10:00:00+08:00",
                    "status": "CONFIRMED",
                    "covered_follow_up_ids": ["F-OTHER-TREATMENT"],
                }
            ]

        self.assertTrue(evaluate_with(mutate).may_send_reminder)

    def test_past_or_cancelled_appointment_does_not_block(self):
        for starts_at, status in (
            ("2030-01-10T10:00:00+08:00", "CONFIRMED"),
            ("2030-01-20T10:00:00+08:00", "CANCELLED"),
        ):
            with self.subTest(starts_at=starts_at, status=status):
                def mutate(data):
                    data["appointments"] = [
                        {
                            "appointment_id": "A1",
                            "starts_at": starts_at,
                            "status": status,
                            "covered_follow_up_ids": ["F-DEMO"],
                        }
                    ]

                self.assertTrue(evaluate_with(mutate).may_send_reminder)

    def test_consent_and_contact_are_channel_specific(self):
        for location, update, reason in (
            ("consent", {"granted": False}, ReasonCode.MESSAGING_CONSENT_NOT_CURRENT),
            ("consent", {"channel": "email"}, ReasonCode.MESSAGING_CONSENT_NOT_CURRENT),
            ("contact", {"is_valid": False}, ReasonCode.CONTACT_DETAILS_INVALID),
            ("contact", {"channel": "email"}, ReasonCode.CONTACT_DETAILS_INVALID),
        ):
            with self.subTest(location=location, update=update):
                result = evaluate_with(
                    lambda data: data["patient"][location].update(update)
                )
                self.assertIn(reason, result.reason_codes)
                self.assertFalse(result.may_send_reminder)

    def test_expired_consent_blocks(self):
        result = evaluate_with(
            lambda data: data["patient"]["consent"].update(
                expires_at="2030-01-15T08:59:59+08:00"
            )
        )
        self.assertIn(ReasonCode.MESSAGING_CONSENT_NOT_CURRENT, result.reason_codes)

    def test_cooldown_sending_window_and_attempt_limit_block(self):
        def mutate(data):
            data["evaluated_at"] = "2030-01-15T21:00:00+08:00"
            data["task"]["next_permitted_contact_at"] = (
                "2030-01-16T09:00:00+08:00"
            )
            data["task"]["contact_attempts"] = 3

        result = evaluate_with(mutate)
        self.assertIn(ReasonCode.CONTACT_TIMING_NOT_PERMITTED, result.reason_codes)
        self.assertIn(ReasonCode.CONTACT_ATTEMPT_LIMIT_REACHED, result.reason_codes)
        self.assertEqual(
            result.reason_codes.count(ReasonCode.CONTACT_TIMING_NOT_PERMITTED), 1
        )

    def test_pending_work_and_record_problems_return_all_reasons(self):
        def mutate(data):
            data["task"]["has_pending_approval"] = True
            data["task"]["has_unresolved_escalation"] = True
            data["requirement"]["source_records_current"] = False
            data["requirement"]["instructions_consistent"] = False

        result = evaluate_with(mutate)
        self.assertEqual(
            set(result.reason_codes),
            {
                ReasonCode.PENDING_APPROVAL_EXISTS,
                ReasonCode.UNRESOLVED_ESCALATION_EXISTS,
                ReasonCode.SOURCE_RECORD_STALE,
                ReasonCode.CLINICAL_INSTRUCTION_REVIEW_REQUIRED,
            },
        )

    def test_closed_requirement_is_not_overdue(self):
        result = evaluate_with(
            lambda data: data["requirement"].update(state="CLOSED")
        )
        self.assertFalse(result.is_overdue)
        self.assertIn(
            ReasonCode.FOLLOW_UP_REQUIREMENT_NOT_OPEN, result.reason_codes
        )

    def test_naive_datetime_is_rejected(self):
        data = copy.deepcopy(BASE_CASE)
        data["evaluated_at"] = "2030-01-15T09:00:00"
        with self.assertRaises(InputValidationError):
            parse_case(data, POLICY)

    def test_unknown_timezone_is_rejected(self):
        policy = dict(POLICY, clinic_timezone="Mars/Olympus_Mons")
        with self.assertRaises(InputValidationError):
            evaluate_case(parse_case(copy.deepcopy(BASE_CASE), policy))

    def test_duplicate_case_ids_are_rejected(self):
        document = {
            "policy_defaults": POLICY,
            "cases": [copy.deepcopy(BASE_CASE), copy.deepcopy(BASE_CASE)],
        }
        with self.assertRaises(InputValidationError):
            parse_cases(document)

    def test_same_patient_multiple_treatments_are_evaluated_independently(self):
        overdue = copy.deepcopy(BASE_CASE)
        overdue["case_id"] = "cleaning-overdue"
        overdue["requirement"]["follow_up_id"] = "F-CLEANING"
        overdue["requirement"]["episode_id"] = "E-CLEANING"
        overdue["episode"]["episode_id"] = "E-CLEANING"

        future = copy.deepcopy(BASE_CASE)
        future["case_id"] = "implant-future"
        future["requirement"]["follow_up_id"] = "F-IMPLANT"
        future["requirement"]["episode_id"] = "E-IMPLANT"
        future["episode"]["episode_id"] = "E-IMPLANT"
        future["requirement"]["approved_due_date"] = "2030-02-01"

        cases = parse_cases(
            {"policy_defaults": POLICY, "cases": [overdue, future]}
        )
        results = [evaluate_case(case) for case in cases]
        self.assertTrue(results[0].may_send_reminder)
        self.assertFalse(results[1].is_overdue)
        self.assertEqual(results[0].patient_id, results[1].patient_id)

    def test_requirement_must_match_supplied_episode(self):
        data = copy.deepcopy(BASE_CASE)
        data["episode"]["episode_id"] = "E-OTHER"
        with self.assertRaises(InputValidationError):
            parse_case(data, POLICY)


class CliTests(unittest.TestCase):
    def test_cli_outputs_json_and_performs_no_action(self):
        path = Path("fixtures/fictional/follow_up_cases.json")
        stdout = StringIO()
        with redirect_stdout(stdout):
            exit_code = run([str(path)])
        self.assertEqual(exit_code, 0)
        result = json.loads(stdout.getvalue())["results"][0]
        self.assertTrue(result["may_send_reminder"])
        self.assertNotIn("action", result)

    def test_cli_returns_safe_error_for_invalid_json(self):
        path = Path("fictional-invalid.json")
        with patch.object(Path, "open", mock_open(read_data="not json")):
            stderr = StringIO()
            with redirect_stderr(stderr):
                exit_code = run([str(path)])
        self.assertEqual(exit_code, 2)
        self.assertIn("error", json.loads(stderr.getvalue()))


if __name__ == "__main__":
    unittest.main()
