// Upande Assessments — Job Applicant client script.
//
// Coexists with upande_ats's Job Applicant JS (Frappe loads both additively).
// The assessment button is gated on the ATS-owned field `ats_result === "Pass"`.
// This app only READS that field; it never writes any `ats_*` field.

frappe.ui.form.on("Job Applicant", {
	refresh(frm) {
		if (frm.is_new()) return;

		const passed_ats = frm.doc.ats_result === "Pass";
		const sent = ["Sent", "Completed", "Passed", "Failed", "Review"].includes(
			frm.doc.custom_assessment_status
		);

		if (passed_ats) {
			const label = sent
				? __("Resend Psychometric Assessment")
				: __("Send Psychometric Assessment");
			frm.add_custom_button(
				label,
				() => send_assessment(frm, "Psychometric", sent),
				__("Assessment")
			);
		}

		// Phase 2 (kiosk UI deferred): returns a tokenised link for HR to open
		// on a shared machine. The engine already handles "Technical" end-to-end.
		frm.add_custom_button(
			__("Generate Technical Assessment"),
			() => send_assessment(frm, "Technical", false),
			__("Assessment")
		);
	},
});

function send_assessment(frm, assessment_type, resend) {
	frappe.call({
		method: "upande_assessments.api.send_assessment",
		args: {
			applicant: frm.doc.name,
			assessment_type: assessment_type,
			resend: resend ? 1 : 0,
		},
		freeze: true,
		freeze_message: __("Preparing assessment..."),
	}).then((r) => {
		const res = r.message;
		if (!res) return;

		if (res.emailed) {
			frappe.show_alert(
				{ message: __("Assessment link emailed to the applicant."), indicator: "green" },
				5
			);
		} else if (res.link) {
			// Technical (kiosk) or applicant without an email: show the link to HR.
			frappe.msgprint({
				title: __("Assessment Link"),
				indicator: "blue",
				message: __(
					"Open this link on the assessment machine:<br><br><a href='{0}' target='_blank'>{0}</a>",
					[res.link]
				),
			});
		}
		frm.reload_doc();
	});
}
