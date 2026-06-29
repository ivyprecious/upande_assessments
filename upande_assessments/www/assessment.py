# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Guest-facing portal page for taking an assessment via a tokenised link.

import frappe
import frappe.sessions
from upande_assessments.api import get_assessment

no_cache = 1


def get_context(context):
	context.no_cache = 1
	context.show_sidebar = False

	token = frappe.form_dict.get("token")
	context.token = token

	if not token:
		context.state = "invalid"
		context.message = "This assessment link is missing its token."
		return context

	try:
		data = get_assessment(token)
	except frappe.ValidationError:
		# get_assessment raises a clean message for bad/invalid tokens.
		context.state = "invalid"
		context.message = frappe.utils.strip_html(
			frappe.message_log[-1].get("message") if frappe.message_log else ""
		) or "This assessment link is not valid."
		frappe.clear_messages()
		frappe.local.response.http_status_code = 200
		return context

	context.state = data.get("state")
	context.title = data.get("title")

	# Both the Start and the Submit actions are guest POSTs that need a CSRF
	# token, so mint one for the intro screen as well as the live assessment.
	# Use get_csrf_token(): it reads session.data.csrf_token and generates +
	# persists one if absent. frappe.session.csrf_token is always None.
	if context.state in ("intro", "open"):
		context.csrf_token = frappe.sessions.get_csrf_token()
		context.applicant_name = data.get("applicant_name")
		context.time_limit_minutes = data.get("time_limit_minutes")

	if context.state == "intro":
		context.instructions = data.get("instructions")

	if context.state == "open":
		context.assessment_type = data.get("assessment_type")
		context.questions = data.get("questions")
		context.remaining_seconds = data.get("remaining_seconds")

	return context
