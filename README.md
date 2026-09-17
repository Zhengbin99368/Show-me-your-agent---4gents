# Dental Patient Follow-Up Agent

This repository contains a safety-first prototype for administrative dental
patient follow-up. The current MVP is deliberately read-only: it evaluates
fictional follow-up requirements and explains whether they are overdue and
whether a reminder would be permitted.

It does **not** diagnose, recommend treatment, send messages, offer live slots,
book appointments, update clinical records, or use real patient information.

See [technical-blueprint.md](technical-blueprint.md) for the complete proposed
architecture and safety boundaries.

## Requirements

- Python 3.11 or newer
- No third-party packages

The examples use only fictional identifiers and dates.

## Run the MVP

From the repository root:

```powershell
python -m followup_agent.cli fixtures/fictional/follow_up_cases.json
```

The command prints JSON with one result per follow-up requirement:

- `is_overdue` uses the strict `approved_due_date < clinic_today` rule;
- `may_send_reminder` applies consent, contact, appointment coverage, timing,
  attempt, approval, escalation, freshness, and consistency checks;
- `reason_codes` explain every applicable blocker;
- `source_versions` identify the evaluated clinical source version.

No action is executed from this output.

## Run verification

```powershell
python -m unittest discover -s tests -v
python -m compileall followup_agent tests
python -m followup_agent.cli fixtures/fictional/follow_up_cases.json
git diff --check
```

## Project structure

```text
followup_agent/
  domain.py       Strict fictional-input parsing and domain types
  eligibility.py  Side-effect-free deterministic eligibility rules
  cli.py          Read-only JSON command-line interface
fixtures/
  fictional/      Synthetic demonstration cases only
tests/             Standard-library unit and CLI tests
```

## Current limits

This is Milestone 1 only. Clinic policy values in the fixture are examples,
not approved operational policy. Before any later integration, the team must
select and approve consent rules, sending windows, cooldowns, contact limits,
templates, escalation ownership, privacy controls, and deployment technology.
