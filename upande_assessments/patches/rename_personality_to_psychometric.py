# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Data migration: the assessment type formerly called "Personality" is now
# "Psychometric". The Select options were renamed in the doctype JSON; this
# patch rewrites existing rows so deployed environments migrate automatically
# on `bench migrate`. Idempotent — reruns are a no-op once no rows match.

import frappe


def execute():
	for doctype in ("Assessment Template", "Assessment Response"):
		if not frappe.db.has_column(doctype, "assessment_type"):
			continue
		frappe.db.sql(
			"""
			UPDATE `tab{0}`
			SET assessment_type = 'Psychometric'
			WHERE assessment_type = 'Personality'
			""".format(doctype)
		)
