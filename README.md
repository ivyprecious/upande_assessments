## Upande Assessments

A standalone Frappe app for candidate assessments — a reusable, template-driven
assessment engine. **Phase 1** ships a Personality / Situational Judgment Test
(SJT); the engine is `assessment_type`-aware so a **Technical** assessment runs
the same flow (kiosk UI deferred to Phase 2).

It is a sibling of `upande_ats` and does **not** depend on or modify it. The only
contact point is the standard `Job Applicant` doctype: this app **reads**
`ats_result == "Pass"` (owned by `upande_ats`) to decide when to offer its button,
and writes its own `custom_assessment_*` fields. It never writes any `ats_*` field.

### Data model (module: Upande Assessments)

- **Assessment Template** — type, optional designation, active flag, pass mark, action on fail.
- **Assessment Question** — belongs to a template, owns an **Assessment Option** child table (per-option `score` + `is_best`).
- **Assessment Response** — one per dispatch: token, status, scoring, result, **Assessment Answer** child rows (snapshots).

HR builds templates, questions, options and per-option scores entirely in Desk.

### Flow

1. On a Job Applicant who passed ATS screening, HR clicks **Send Personality Assessment**.
2. `send_assessment` picks the active template (Job Opening override → Designation → global default), creates a Response with a single-use token + expiry, and emails the tokenised link (Technical returns the link for a kiosk).
3. The applicant opens the link as a **guest** (no login), answers, and submits.
4. `submit_assessment` scores **server-side** from each option's `score`, snapshots the chosen text, computes the result against the pass mark + `action_on_fail`, updates `custom_assessment_*` on the applicant, and notifies HR.

Correct answers and per-option scores never leave the server: the guest
`get_assessment` endpoint returns option text only.

### Server logic

`upande_assessments/api.py`:
- `send_assessment(applicant, assessment_type="Personality", resend=0)` — HR only.
- `get_assessment(token)` — guest; render-only payload, no answer keys.
- `submit_assessment(token, answers)` — guest; idempotent, scores server-side.

Guest portal: `upande_assessments/www/assessment.{html,py}`.

### Configuration left to HR

- **Pass mark** (`pass_percentage`) and **content** (questions/options/scores) are entered in Desk. With no pass mark set, results default to **Review**.
- Link validity is `DEFAULT_EXPIRY_DAYS` in `api.py` (default 14).

### Install (local dev)

```bash
bench --site <site> install-app upande_assessments
bench --site <site> migrate
```

Run with `developer_mode = 1` so doctypes export to the app and can be committed.

### Deploy to staging

```bash
git pull
bench --site <site> migrate     # imports doctypes, runs after_migrate (custom fields)
bench build --app upande_assessments
bench restart                   # pick up JS assets
```

Custom fields and the invite Email Template ship as fixtures
(`custom_assessment_*` only) and re-apply on every migrate.

### Contributing

This app uses `pre-commit` for code formatting and linting:

```bash
cd apps/upande_assessments
pre-commit install
```

### License

mit
