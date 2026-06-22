# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Guest-facing portal page for taking an assessment via a tokenised link.

import frappe
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

	if context.state == "open":
		context.title = data.get("title")
		context.assessment_type = data.get("assessment_type")
		context.applicant_name = data.get("applicant_name")
		context.questions = data.get("questions")
		# csrf token so the guest can POST back to submit_assessment.
		context.csrf_token = frappe.session.csrf_token

	return context
