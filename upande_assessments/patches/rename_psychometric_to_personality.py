# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Data migration: the assessment type user-facing label "Psychometric" is now
# "Personality". The Select option value was renamed in the doctype JSON (the
# custom_psychometric_* fieldnames on Job Applicant are deliberately KEPT, since
# they store status/score values, not the type string). This patch reloads the
# affected doctypes so the new Select options are in place, then rewrites any
# existing rows still holding the old value.
# Idempotent — reruns are a no-op once no rows match.

import frappe


def execute():
	frappe.reload_doc("upande_assessments", "doctype", "assessment_template")
	frappe.reload_doc("upande_assessments", "doctype", "assessment_response")

	for doctype in ("Assessment Template", "Assessment Response"):
		if not frappe.db.has_column(doctype, "assessment_type"):
			continue
		frappe.db.sql(
			"""
			UPDATE `tab{0}`
			SET assessment_type = 'Personality'
			WHERE assessment_type = 'Psychometric'
			""".format(doctype)
		)
