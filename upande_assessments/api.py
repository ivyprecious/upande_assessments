# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Whitelisted server logic for Upande Assessments.
#
# Trust boundary: get_assessment / submit_assessment are guest endpoints. They
# validate the token FIRST, then perform writes with ignore_permissions=True.
# Correct answers and per-option scores never leave the server.

import json

import frappe
from frappe import _
from frappe.utils import (
	add_days,
	get_datetime,
	get_url,
	getdate,
	now_datetime,
	nowdate,
)

# Fallback link lifetime, used only when a template has no expiry_days set.
# The real value is now HR-configurable per template (Assessment Template.expiry_days).
DEFAULT_EXPIRY_DAYS = 14

ASSESSMENT_PAGE = "assessment"


# ---------------------------------------------------------------------------
# Template selection
# ---------------------------------------------------------------------------
def pick_template(applicant_doc, assessment_type):
	"""Choose the active template for an applicant.

	Precedence: Job Opening override -> Designation match -> global default.
	(The Job Opening override slot is reserved for Phase 2; today it falls
	through to Designation, then to a global active template.)
	"""
	base = {"is_active": 1, "assessment_type": assessment_type}

	# Designation-specific template.
	if applicant_doc.get("designation"):
		name = frappe.db.get_value(
			"Assessment Template",
			dict(base, designation=applicant_doc.designation),
		)
		if name:
			return name

	# Global default: active template of this type with no designation set.
	name = frappe.db.get_value("Assessment Template", dict(base, designation=["in", [None, ""]]))
	if name:
		return name

	# Last resort: any active template of this type.
	return frappe.db.get_value("Assessment Template", base)


# ---------------------------------------------------------------------------
# HR-triggered: create + dispatch an assessment
# ---------------------------------------------------------------------------
@frappe.whitelist()
def send_assessment(applicant, assessment_type="Psychometric", resend=0):
	"""Create an Assessment Response and dispatch the tokenised link.

	Psychometric -> emailed to the applicant.
	Technical    -> link returned to HR for the kiosk machine.
	"""
	# Only users who can edit the applicant (HR roles) may dispatch assessments.
	if not frappe.has_permission("Job Applicant", "write", doc=applicant):
		frappe.throw(_("Not permitted to send assessments for this applicant."), frappe.PermissionError)

	resend = int(resend or 0)
	applicant_doc = frappe.get_doc("Job Applicant", applicant)

	template = pick_template(applicant_doc, assessment_type)
	if not template:
		frappe.throw(
			_("No active {0} assessment template found. Create one in Desk first.").format(
				assessment_type
			)
		)

	template_doc = frappe.get_cached_doc("Assessment Template", template)
	# Deadline is driven by the template; fall back to the module default if HR
	# has not set expiry_days on it yet.
	expiry_days = template_doc.expiry_days or DEFAULT_EXPIRY_DAYS

	# Block duplicates unless this is an explicit resend.
	existing = frappe.db.get_value(
		"Assessment Response",
		{
			"job_applicant": applicant,
			"assessment_type": assessment_type,
			"status": ["in", ["Sent", "In Progress"]],
		},
		["name", "token", "status"],
		as_dict=True,
	)
	if existing and not resend:
		frappe.throw(
			_("A {0} assessment is already pending for this applicant ({1}). Use Resend to send a fresh link.").format(
				assessment_type, existing.name
			)
		)

	# On resend, expire the old pending response so its token can no longer be used.
	if existing and resend:
		frappe.db.set_value("Assessment Response", existing.name, "status", "Expired")

	response = frappe.get_doc(
		{
			"doctype": "Assessment Response",
			"job_applicant": applicant,
			"assessment_template": template,
			"assessment_type": assessment_type,
			"token": frappe.generate_hash(length=32),
			"status": "Sent",
			"sent_on": now_datetime(),
			"expiry_date": add_days(nowdate(), expiry_days),
		}
	)
	response.insert(ignore_permissions=True)

	frappe.db.set_value("Job Applicant", applicant, "custom_assessment_status", "Sent")

	link = _assessment_link(response.token)

	emailed = False
	if assessment_type == "Psychometric":
		emailed = _email_invite(applicant_doc, response, link, template_doc)

	frappe.db.commit()

	return {
		"response": response.name,
		"link": link,
		"emailed": emailed,
		"assessment_type": assessment_type,
	}


# ---------------------------------------------------------------------------
# HR-triggered: bulk dispatch from the Job Applicant list view
# ---------------------------------------------------------------------------
# Above this many applicants we run the loop in a background job so the web
# request doesn't time out.
BULK_INLINE_THRESHOLD = 30

# An applicant already holding a response in one of these states must not be
# re-sent to in bulk (avoids spamming). Resend stays a deliberate per-applicant
# action.
_ACTIVE_RESPONSE_STATES = ["Sent", "In Progress", "Completed"]


@frappe.whitelist()
def bulk_send_assessment(applicants, assessment_type="Psychometric"):
	"""Send a Psychometric assessment to every eligible applicant in a selection.

	The list-view selection is never trusted: the eligibility gate (passed ATS,
	not already sent) is re-applied server-side for each name. Eligible ones go
	through the existing ``send_assessment``; everyone else is reported back,
	never silently dropped.

	Small batches run inline and return the summary. Batches larger than
	``BULK_INLINE_THRESHOLD`` are enqueued and run in the background.
	"""
	# HR-only. The per-applicant button checks write permission on a single doc;
	# the list action runs without one, so we check at the doctype level.
	if not frappe.has_permission("Job Applicant", "write"):
		frappe.throw(_("Not permitted to send assessments."), frappe.PermissionError)

	if isinstance(applicants, str):
		applicants = json.loads(applicants)
	# Drop blanks and de-duplicate while preserving order.
	applicants = list(dict.fromkeys(a for a in (applicants or []) if a))
	if not applicants:
		frappe.throw(_("Select at least one applicant."))

	if len(applicants) > BULK_INLINE_THRESHOLD:
		frappe.enqueue(
			"upande_assessments.api._run_bulk_send",
			queue="long",
			timeout=1500,
			applicants=applicants,
			assessment_type=assessment_type,
		)
		return {"queued": True, "count": len(applicants)}

	return _run_bulk_send(applicants, assessment_type)


@frappe.whitelist()
def send_to_all_passed(job_opening, assessment_type="Psychometric"):
	"""Send to every applicant on a Job Opening who passed ATS screening.

	Convenience wrapper over ``bulk_send_assessment`` for HR who always send per
	role and don't want to hand-select. The full eligibility gate still runs in
	``bulk_send_assessment``, so applicants who were already sent to are skipped
	here too — this never resends.
	"""
	if not frappe.has_permission("Job Applicant", "write"):
		frappe.throw(_("Not permitted to send assessments."), frappe.PermissionError)

	applicants = frappe.get_all(
		"Job Applicant",
		filters={"job_title": job_opening, "ats_result": "Pass"},
		pluck="name",
	)
	if not applicants:
		return {"none_passed": True}

	return bulk_send_assessment(applicants, assessment_type=assessment_type)


def _run_bulk_send(applicants, assessment_type="Psychometric"):
	"""Walk the selection, applying the eligibility gate to each applicant.

	Returns a summary dict.
	"""
	summary = {
		"sent": [],
		"skipped_not_passed": [],
		"skipped_already_sent": [],
		"failed": [],
	}

	for applicant in applicants:
		info = frappe.db.get_value(
			"Job Applicant", applicant, ["ats_result", "applicant_name"], as_dict=True
		)
		label = (info and info.applicant_name) or applicant

		# Gate 1: must have passed ATS screening.
		if not info or info.ats_result != "Pass":
			summary["skipped_not_passed"].append(label)
			continue

		# Gate 2: no active or completed response of this type already exists.
		if frappe.db.exists(
			"Assessment Response",
			{
				"job_applicant": applicant,
				"assessment_type": assessment_type,
				"status": ["in", _ACTIVE_RESPONSE_STATES],
			},
		):
			summary["skipped_already_sent"].append(label)
			continue

		# Eligible: dispatch via the shared single-applicant path. One bad record
		# must not abort the rest of the batch.
		try:
			send_assessment(applicant, assessment_type=assessment_type)
			summary["sent"].append(label)
		except Exception as e:
			frappe.log_error(
				title=f"bulk_send_assessment failed for {applicant}",
				message=frappe.get_traceback(),
			)
			summary["failed"].append({"applicant": label, "reason": str(e)})

	return summary


def _assessment_link(token):
	return get_url(f"/{ASSESSMENT_PAGE}?token={token}")


def _email_invite(applicant_doc, response, link, template_doc):
	recipient = applicant_doc.get("email_id")
	if not recipient:
		return False

	# Instructions + time limit come from the one Template field so the email and
	# the portal never drift. time_limit_minutes of 0/None means "no time limit".
	time_limit = template_doc.get("time_limit_minutes")
	args = {
		"applicant_name": applicant_doc.get("applicant_name") or "Candidate",
		"link": link,
		"expiry_date": frappe.utils.formatdate(response.expiry_date),
		"deadline": frappe.utils.formatdate(response.expiry_date),
		"instructions": template_doc.get("instructions") or "",
		"time_limit_minutes": time_limit or 0,
		"time_limit_label": (
			_("{0} minutes").format(time_limit) if time_limit else _("no time limit")
		),
	}

	# Prefer an HR-editable Email Template if present; otherwise fall back to
	# a built-in message so the flow works out of the box.
	template = frappe.db.exists("Email Template", "Assessment Invitation")
	if template:
		et = frappe.get_doc("Email Template", "Assessment Invitation")
		subject = frappe.render_template(et.subject, args)
		message = frappe.render_template(et.response_html or et.response or "", args)
	else:
		subject = _("You have been invited to complete an assessment")
		message = _DEFAULT_INVITE_HTML.format(**args)

	frappe.sendmail(
		recipients=[recipient],
		subject=subject,
		message=message,
		reference_doctype="Assessment Response",
		reference_name=response.name,
	)
	return True


_DEFAULT_INVITE_HTML = """
<p>Dear {applicant_name},</p>
<p>You have been invited to complete an assessment as part of your application.</p>
{instructions}
<p>⏱ Once you click Start you have <b>{time_limit_label}</b>. The assessment submits
automatically when time runs out.</p>
<p>Please use the link below. It is personal to you and can be submitted only once.
The link is valid until <b>{deadline}</b>.</p>
<p><a href="{link}"
   style="display:inline-block;padding:10px 18px;background:#2490ef;color:#fff;
   border-radius:6px;text-decoration:none;">Start Assessment</a></p>
<p>If the button does not work, copy this link into your browser:<br>{link}</p>
<p>Good luck!</p>
"""


# ---------------------------------------------------------------------------
# Guest endpoint: fetch the assessment for rendering (NO answer keys)
# ---------------------------------------------------------------------------
@frappe.whitelist(allow_guest=True)
def get_assessment(token):
	"""Return the state needed to render the portal page.

	Before Start (no ``started_on``): returns ``state == "intro"`` with the
	template instructions + time limit only — NO questions. After Start (a
	refresh mid-assessment): returns ``state == "open"`` with questions and the
	real remaining time, computed server-side from ``started_on`` so a refresh
	resumes rather than resets. Never returns score, is_best, max_score or
	anything revealing the right answer.
	"""
	response = _get_response_by_token(token)

	if response.status == "Completed":
		return {"state": "completed"}

	if _is_expired(response):
		if response.status != "Expired":
			frappe.db.set_value("Assessment Response", response.name, "status", "Expired")
			frappe.db.commit()
		return {"state": "expired"}

	template = frappe.db.get_value(
		"Assessment Template",
		response.assessment_template,
		["title", "assessment_type", "instructions", "time_limit_minutes"],
		as_dict=True,
	)

	base = {
		"title": template.title,
		"assessment_type": template.assessment_type,
		"instructions": template.instructions,
		"time_limit_minutes": template.time_limit_minutes,
		"applicant_name": frappe.db.get_value(
			"Job Applicant", response.job_applicant, "applicant_name"
		),
	}

	# Not started yet -> instructions / pre-start screen. Questions stay hidden
	# until the candidate clicks Start (which calls start_assessment).
	if not response.started_on:
		return {"state": "intro", **base}

	# Already started -> resume with the real remaining time.
	base["questions"] = _load_questions(response.assessment_template)
	base["remaining_seconds"] = _remaining_seconds(response, template.time_limit_minutes)
	return {"state": "open", **base}


@frappe.whitelist(allow_guest=True)
def start_assessment(token):
	"""Stamp ``started_on`` server-side and return the questions to render.

	The countdown is seeded from time computed here, never from the browser.
	Idempotent: a re-click or refresh-then-start never resets an existing
	``started_on``.
	"""
	response = _get_response_by_token(token)

	if response.status == "Completed":
		return {"state": "completed"}

	if _is_expired(response):
		frappe.db.set_value("Assessment Response", response.name, "status", "Expired")
		frappe.db.commit()
		return {"state": "expired"}

	# Stamp the start once. Do not trust the browser clock.
	if not response.started_on:
		frappe.db.set_value(
			"Assessment Response",
			response.name,
			{"started_on": now_datetime(), "status": "In Progress"},
		)
		frappe.db.commit()
		response.reload()

	time_limit_minutes = frappe.db.get_value(
		"Assessment Template", response.assessment_template, "time_limit_minutes"
	)

	return {
		"state": "open",
		"questions": _load_questions(response.assessment_template),
		"time_limit_minutes": time_limit_minutes,
		"remaining_seconds": _remaining_seconds(response, time_limit_minutes),
	}


def _load_questions(template_name):
	"""Questions + options for rendering only — no score, no is_best, no red flag."""
	questions = frappe.get_all(
		"Assessment Question",
		filters={"assessment_template": template_name},
		fields=["name", "question_text", "question_type", "sequence"],
		order_by="sequence asc, creation asc",
	)
	for q in questions:
		q["options"] = frappe.get_all(
			"Assessment Option",
			filters={"parent": q["name"], "parenttype": "Assessment Question"},
			fields=["name", "option_text"],
			order_by="idx asc",
		)
	return questions


def _remaining_seconds(response, time_limit_minutes):
	"""Whole seconds left from time_limit_minutes minus elapsed-since-started_on.

	Returns ``None`` when no limit is configured (untimed assessment), and never
	a negative number.
	"""
	if not time_limit_minutes or not response.started_on:
		return None
	elapsed = (now_datetime() - get_datetime(response.started_on)).total_seconds()
	return max(0, int(time_limit_minutes * 60 - elapsed))


# ---------------------------------------------------------------------------
# Guest endpoint: submit answers, score server-side
# ---------------------------------------------------------------------------
@frappe.whitelist(allow_guest=True)
def submit_assessment(token, answers):
	"""Score answers server-side and finalise the response. Idempotent."""
	response = _get_response_by_token(token)

	if response.status == "Completed":
		frappe.throw(_("This assessment has already been submitted."), title=_("Already Submitted"))

	if _is_expired(response):
		frappe.db.set_value("Assessment Response", response.name, "status", "Expired")
		frappe.db.commit()
		frappe.throw(_("This assessment link has expired."), title=_("Expired"))

	if isinstance(answers, str):
		answers = json.loads(answers)
	# Map question name -> chosen option row name.
	chosen = {a.get("question"): a.get("option") for a in answers if a.get("question")}

	template = frappe.get_doc("Assessment Template", response.assessment_template)
	questions = frappe.get_all(
		"Assessment Question",
		filters={"assessment_template": response.assessment_template},
		fields=["name", "question_text"],
		order_by="sequence asc, creation asc",
	)

	doc = frappe.get_doc("Assessment Response", response.name)

	# The server is the source of truth for time; the browser timer is UX only.
	# Independently check elapsed-since-started_on against the limit. A late
	# submission (even past the network grace) is still accepted — we simply
	# score the answers that were given, which the loop below already does, so
	# there is nothing to reject here. Logged for HR visibility when over.
	_log_if_over_time(doc, template)

	doc.set("answers", [])
	total_score = 0.0
	max_score = 0.0
	any_red_flag = False

	for q in questions:
		options = frappe.get_all(
			"Assessment Option",
			filters={"parent": q["name"], "parenttype": "Assessment Question"},
			fields=["name", "option_text", "score", "is_red_flag"],
		)
		if options:
			max_score += max(o["score"] for o in options)

		picked_name = chosen.get(q["name"])
		picked = next((o for o in options if o["name"] == picked_name), None)

		score_awarded = picked["score"] if picked else 0.0
		total_score += score_awarded

		picked_red_flag = bool(picked and picked["is_red_flag"])
		if picked_red_flag:
			any_red_flag = True

		doc.append(
			"answers",
			{
				"question": q["name"],
				"question_text": q["question_text"],
				"selected_option_text": picked["option_text"] if picked else None,
				"score_awarded": score_awarded,
				# Snapshot so HR can see which answer tripped the flag.
				"is_red_flag": 1 if picked_red_flag else 0,
			},
		)

	percentage = (total_score / max_score * 100.0) if max_score else 0.0
	result = _resolve_result(percentage, template)

	# A red-flag option forces Review regardless of score. The numeric score is
	# left untouched — only the verdict changes.
	if any_red_flag:
		result = "Review"

	doc.total_score = total_score
	doc.max_score = max_score
	doc.percentage = percentage
	doc.result = result
	doc.flagged = 1 if any_red_flag else 0
	doc.status = "Completed"
	doc.completed_on = now_datetime()
	doc.save(ignore_permissions=True)

	_update_applicant(response.job_applicant, result, percentage, response.assessment_type)

	frappe.db.commit()
	return {"state": "submitted"}


# Network slack allowed on top of the time limit before a submission counts as
# "over time". The submission is accepted either way; this only affects logging.
SUBMIT_GRACE_SECONDS = 45


def _log_if_over_time(response, template):
	"""Record (don't reject) submissions that arrive after the time limit."""
	limit = template.get("time_limit_minutes")
	if not limit or not response.started_on:
		return
	elapsed = (now_datetime() - get_datetime(response.started_on)).total_seconds()
	if elapsed > limit * 60 + SUBMIT_GRACE_SECONDS:
		frappe.log_error(
			title=f"Assessment submitted over time: {response.name}",
			message=(
				f"Response {response.name} submitted after {int(elapsed)}s "
				f"(limit {limit*60}s + {SUBMIT_GRACE_SECONDS}s grace). Accepted."
			),
		)


def _resolve_result(percentage, template):
	"""Map a percentage to Pass / Fail / Review.

	If HR has not set a pass mark yet, we cannot decide -> Review.
	Below the pass mark: Reject -> Fail, otherwise Flag for Review.
	"""
	if not template.pass_percentage:
		return "Review"
	if percentage >= template.pass_percentage:
		return "Pass"
	return "Fail" if template.action_on_fail == "Reject" else "Review"


def _update_applicant(applicant, result, percentage, assessment_type=None):
	"""Write only this app's custom fields. Never the core Job Applicant status.

	Routes the result to the type-specific pair (Psychometric / Technical) and
	always mirrors it to the generic pair as the latest result, which powers the
	Job Applicant list-view column.
	"""
	status_map = {"Pass": "Passed", "Fail": "Failed", "Review": "Review"}
	status = status_map.get(result, "Completed")

	# Generic fields always reflect the latest completed assessment.
	values = {
		"custom_assessment_status": status,
		"custom_assessment_score": percentage,
	}

	# Route to the type-specific pair; unknown types write only the generic fields.
	type_fields = {
		"Psychometric": ("custom_psychometric_status", "custom_psychometric_score"),
		"Technical": ("custom_technical_status", "custom_technical_score"),
	}
	pair = type_fields.get(assessment_type)
	if pair:
		status_field, score_field = pair
		values[status_field] = status
		values[score_field] = percentage

	frappe.db.set_value(
		"Job Applicant",
		applicant,
		values,
		update_modified=False,
	)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _get_response_by_token(token):
	if not token:
		frappe.throw(_("Missing assessment token."), title=_("Invalid Link"))

	name = frappe.db.get_value("Assessment Response", {"token": token}, "name")
	if not name:
		frappe.throw(_("This assessment link is not valid."), title=_("Invalid Link"))

	return frappe.get_doc("Assessment Response", name)


def _is_expired(response):
	if response.status == "Expired":
		return True
	if response.expiry_date and getdate(response.expiry_date) < getdate(nowdate()):
		return True
	return False
