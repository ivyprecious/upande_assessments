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
	]
}


def after_install():
	_sync_custom_fields()


def after_migrate():
	_sync_custom_fields()


def _sync_custom_fields():
	create_custom_fields(JOB_APPLICANT_CUSTOM_FIELDS, ignore_validate=True)
	frappe.db.commit()
