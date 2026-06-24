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
	get_url,
	getdate,
	now_datetime,
	nowdate,
)

# How long a tokenised link stays valid. Kept here (not hardcoded in records)
# so it is easy to tune; pass mark and content remain HR-configurable in Desk.
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
def send_assessment(applicant, assessment_type="Personality", resend=0):
	"""Create an Assessment Response and dispatch the tokenised link.

	Personality -> emailed to the applicant.
	Technical   -> link returned to HR for the kiosk machine.
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
			"expiry_date": add_days(nowdate(), DEFAULT_EXPIRY_DAYS),
		}
	)
	response.insert(ignore_permissions=True)

	frappe.db.set_value("Job Applicant", applicant, "custom_assessment_status", "Sent")

	link = _assessment_link(response.token)

	emailed = False
	if assessment_type == "Personality":
		emailed = _email_invite(applicant_doc, response, link)

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
# request doesn't time out; HR is emailed the summary on completion.
BULK_INLINE_THRESHOLD = 30

# An applicant already holding a response in one of these states must not be
# re-sent to in bulk (avoids spamming). Resend stays a deliberate per-applicant
# action.
_ACTIVE_RESPONSE_STATES = ["Sent", "In Progress", "Completed"]


@frappe.whitelist()
def bulk_send_assessment(applicants, assessment_type="Personality"):
	"""Send a Personality assessment to every eligible applicant in a selection.

	The list-view selection is never trusted: the eligibility gate (passed ATS,
	not already sent) is re-applied server-side for each name. Eligible ones go
	through the existing ``send_assessment``; everyone else is reported back,
	never silently dropped.

	Small batches run inline and return the summary. Batches larger than
	``BULK_INLINE_THRESHOLD`` are enqueued and HR is emailed on completion.
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
			notify_user=frappe.session.user,
		)
		return {"queued": True, "count": len(applicants)}

	return _run_bulk_send(applicants, assessment_type)


@frappe.whitelist()
def send_to_all_passed(job_opening, assessment_type="Personality"):
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


def _run_bulk_send(applicants, assessment_type="Personality", notify_user=None):
	"""Walk the selection, applying the eligibility gate to each applicant.

	Returns a summary dict. ``notify_user`` is set only on the background path,
	where there is no caller waiting on the return value, so we email it instead.
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
		# (e.g. a missing email) must not abort the rest of the batch.
		try:
			res = send_assessment(applicant, assessment_type=assessment_type)
			# Personality is delivered by email; if nothing was sent the applicant
			# has no address. Report it as a failure so HR can fix and resend.
			if assessment_type == "Personality" and not res.get("emailed"):
				summary["failed"].append({"applicant": label, "reason": _("No email address")})
			else:
				summary["sent"].append(label)
		except Exception as e:
			frappe.log_error(
				title=f"bulk_send_assessment failed for {applicant}",
				message=frappe.get_traceback(),
			)
			summary["failed"].append({"applicant": label, "reason": str(e)})

	if notify_user:
		_notify_bulk_complete(notify_user, summary)

	return summary


def _notify_bulk_complete(user, summary):
	"""Email the HR user who triggered a background bulk send its outcome."""
	recipient = frappe.db.get_value("User", user, "email") or user
	if not recipient or recipient in ("Administrator", "Guest"):
		return

	failed_lines = "".join(
		f"<li>{f['applicant']} — {f['reason']}</li>" for f in summary["failed"]
	)
	message = _(
		"<p>The bulk assessment send has finished.</p>"
		"<ul>"
		"<li><b>Sent:</b> {sent}</li>"
		"<li><b>Skipped (already sent):</b> {already}</li>"
		"<li><b>Skipped (not passed):</b> {not_passed}</li>"
		"<li><b>Failed:</b> {failed}</li>"
		"</ul>"
	).format(
		sent=len(summary["sent"]),
		already=len(summary["skipped_already_sent"]),
		not_passed=len(summary["skipped_not_passed"]),
		failed=len(summary["failed"]),
	)
	if failed_lines:
		message += _("<p>Failed applicants (please check their email address):</p><ul>{0}</ul>").format(
			failed_lines
		)

	frappe.sendmail(
		recipients=[recipient],
		subject=_("Bulk assessment send complete — {0} sent, {1} failed").format(
			len(summary["sent"]), len(summary["failed"])
		),
		message=message,
	)


def _assessment_link(token):
	return get_url(f"/{ASSESSMENT_PAGE}?token={token}")


def _email_invite(applicant_doc, response, link):
	recipient = applicant_doc.get("email_id")
	if not recipient:
		return False

	args = {
		"applicant_name": applicant_doc.get("applicant_name") or "Candidate",
		"link": link,
		"expiry_date": frappe.utils.formatdate(response.expiry_date),
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
<p>Please use the link below. It is personal to you and can be submitted only once.
The link is valid until <b>{expiry_date}</b>.</p>
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
	"""Return template + questions + options for rendering only.

	Never returns score, is_best, max_score or anything revealing the right
	answer. Raises a clean state for invalid / expired / completed tokens.
	"""
	response = _get_response_by_token(token)

	if response.status == "Completed":
		return {"state": "completed"}

	if _is_expired(response):
		if response.status != "Expired":
			frappe.db.set_value("Assessment Response", response.name, "status", "Expired")
			frappe.db.commit()
		return {"state": "expired"}

	# First open flips Sent -> In Progress.
	if response.status == "Sent":
		frappe.db.set_value("Assessment Response", response.name, "status", "In Progress")
		frappe.db.commit()

	template = frappe.db.get_value(
		"Assessment Template",
		response.assessment_template,
		["title", "assessment_type"],
		as_dict=True,
	)

	questions = frappe.get_all(
		"Assessment Question",
		filters={"assessment_template": response.assessment_template},
		fields=["name", "question_text", "question_type", "sequence"],
		order_by="sequence asc, creation asc",
	)

	for q in questions:
		# Expose only the option row name + display text. No score, no is_best.
		q["options"] = frappe.get_all(
			"Assessment Option",
			filters={"parent": q["name"], "parenttype": "Assessment Question"},
			fields=["name", "option_text"],
			order_by="idx asc",
		)

	return {
		"state": "open",
		"title": template.title,
		"assessment_type": template.assessment_type,
		"applicant_name": frappe.db.get_value(
			"Job Applicant", response.job_applicant, "applicant_name"
		),
		"questions": questions,
	}


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
	doc.set("answers", [])
	total_score = 0.0
	max_score = 0.0

	for q in questions:
		options = frappe.get_all(
			"Assessment Option",
			filters={"parent": q["name"], "parenttype": "Assessment Question"},
			fields=["name", "option_text", "score"],
		)
		if options:
			max_score += max(o["score"] for o in options)

		picked_name = chosen.get(q["name"])
		picked = next((o for o in options if o["name"] == picked_name), None)

		score_awarded = picked["score"] if picked else 0.0
		total_score += score_awarded

		doc.append(
			"answers",
			{
				"question": q["name"],
				"question_text": q["question_text"],
				"selected_option_text": picked["option_text"] if picked else None,
				"score_awarded": score_awarded,
			},
		)

	percentage = (total_score / max_score * 100.0) if max_score else 0.0
	result = _resolve_result(percentage, template)

	doc.total_score = total_score
	doc.max_score = max_score
	doc.percentage = percentage
	doc.result = result
	doc.status = "Completed"
	doc.completed_on = now_datetime()
	doc.save(ignore_permissions=True)

	_update_applicant(response.job_applicant, result, percentage)

	frappe.db.commit()
	return {"state": "submitted"}


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


def _update_applicant(applicant, result, percentage):
	"""Write only this app's custom fields. Never the core Job Applicant status."""
	status_map = {"Pass": "Passed", "Fail": "Failed", "Review": "Review"}
	frappe.db.set_value(
		"Job Applicant",
		applicant,
		{
			"custom_assessment_status": status_map.get(result, "Completed"),
			"custom_assessment_score": percentage,
		},
	)


def _hr_recipients():
	users = frappe.get_all(
		"Has Role",
		filters={"role": "Group HR Manager", "parenttype": "User"},
		pluck="parent",
	)
	emails = [
		u
		for u in users
		if u not in ("Administrator", "Guest")
		and frappe.db.get_value("User", u, "enabled")
	]
	return list(set(emails))


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
