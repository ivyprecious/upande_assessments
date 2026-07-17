import frappe


def execute():
    """Recreate the site-created 'Talent Pool Report' that the orphan sweep
    deleted, so upande_ats after_migrate can save its workspace. Stub only —
    the real definition is rebuilt in the UI once the site is back."""
    if frappe.db.exists("Report", "Talent Pool Report"):
        return
    frappe.get_doc({
        "doctype": "Report",
        "report_name": "Talent Pool Report",
        "ref_doctype": "Job Applicant",
        "report_type": "Report Builder",
        "is_standard": "No",
        "module": "Upande Assessments",
    }).insert(ignore_permissions=True)
    frappe.db.commit()
