# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

# Custom fields this app owns on Job Applicant.
# Prefix `custom_assessment_*` keeps them distinct from upande_ats's `ats_*` fields.
JOB_APPLICANT_CUSTOM_FIELDS = {
	"Job Applicant": [
		{
			"fieldname": "custom_assessment_section",
			"label": "Assessment",
			"fieldtype": "Section Break",
			"insert_after": "status",
			"collapsible": 1,
		},
		{
			"fieldname": "custom_assessment_status",
			"label": "Assessment Status",
			"fieldtype": "Select",
			"options": "Not Sent\nSent\nCompleted\nPassed\nFailed\nReview",
			"default": "Not Sent",
			"insert_after": "custom_assessment_section",
			"in_standard_filter": 1,
			"read_only": 1,
			"allow_on_submit": 1,
		},
		{
			"fieldname": "custom_assessment_score",
			"label": "Assessment Score (%)",
			"fieldtype": "Float",
			"insert_after": "custom_assessment_status",
			"read_only": 1,
			"allow_on_submit": 1,
		},
		# Psychometric-specific result, kept distinct from the generic latest-result
		# fields above so HR can see both assessment types side by side.
		{
			"fieldname": "custom_psychometric_column",
			"label": "Psychometric Assessment",
			"fieldtype": "Column Break",
			"insert_after": "custom_assessment_score",
		},
		{
			"fieldname": "custom_psychometric_status",
			"label": "Psychometric Status",
			"fieldtype": "Select",
			"options": "Not Sent\nSent\nCompleted\nPassed\nFailed\nReview",
			"default": "Not Sent",
			"insert_after": "custom_psychometric_column",
			"read_only": 1,
			"allow_on_submit": 1,
		},
		{
			"fieldname": "custom_psychometric_score",
			"label": "Psychometric Score (%)",
			"fieldtype": "Float",
			"insert_after": "custom_psychometric_status",
			"read_only": 1,
			"allow_on_submit": 1,
		},
		# Technical-specific result.
		{
			"fieldname": "custom_technical_column",
			"label": "Technical Assessment",
			"fieldtype": "Column Break",
			"insert_after": "custom_psychometric_score",
		},
		{
			"fieldname": "custom_technical_status",
			"label": "Technical Status",
			"fieldtype": "Select",
			"options": "Not Sent\nSent\nCompleted\nPassed\nFailed\nReview",
			"default": "Not Sent",
			"insert_after": "custom_technical_column",
			"read_only": 1,
			"allow_on_submit": 1,
		},
		{
			"fieldname": "custom_technical_score",
			"label": "Technical Score (%)",
			"fieldtype": "Float",
			"insert_after": "custom_technical_status",
			"read_only": 1,
			"allow_on_submit": 1,
		},
	]
}


def after_install():
	_sync_custom_fields()


def after_migrate():
	_sync_custom_fields()


def _sync_custom_fields():
	create_custom_fields(JOB_APPLICANT_CUSTOM_FIELDS, ignore_validate=True)
	frappe.db.commit()
