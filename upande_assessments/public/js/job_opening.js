// Upande Assessments — Job Opening client script.
//
// Adds a "Send to All Passed" button so HR can dispatch the Personality
// assessment to every applicant on this opening who passed ATS screening,
// without hand-selecting in the list view. Thin wrapper over the same
// bulk_send_assessment path — eligibility (passed + not already sent) is
// re-checked server-side, so this never resends.
//
// Coexists with upande_ats's Job Opening script: form handlers stack
// additively, so both run.

frappe.ui.form.on("Job Opening", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(
			__("Send to All Passed"),
			() => {
				frappe.confirm(
					__(
						"Send the Personality assessment to every applicant on this opening who passed ATS screening (skipping anyone already sent to)?"
					),
					() => send_to_all_passed(frm)
				);
			},
			__("Assessment")
		);
	},
});

function send_to_all_passed(frm) {
	frappe.call({
		method: "upande_assessments.api.send_to_all_passed",
		args: { job_opening: frm.doc.name },
		freeze: true,
		freeze_message: __("Sending assessments…"),
		callback: (r) => show_bulk_summary(r.message),
	});
}

function show_bulk_summary(msg) {
	if (!msg) return;

	if (msg.none_passed) {
		frappe.msgprint({
			title: __("Nothing to send"),
			indicator: "blue",
			message: __("No applicants have passed ATS screening for this opening yet."),
		});
		return;
	}

	// Large batches run in the background.
	if (msg.queued) {
		frappe.msgprint({
			title: __("Queued"),
			indicator: "blue",
			message: __(
				"Sending {0} assessments in the background. This may take a few minutes to finish.",
				[msg.count]
			),
		});
		return;
	}

	const parts = [
		__("Sent {0}", [msg.sent.length]),
		__("Skipped {0} (already sent)", [msg.skipped_already_sent.length]),
		__("Skipped {0} (not passed)", [msg.skipped_not_passed.length]),
		__("Failed {0}", [msg.failed.length]),
	];

	let message = parts.join(" · ");
	if (msg.failed.length) {
		const rows = msg.failed
			.map((f) => `<li>${frappe.utils.escape_html(f.applicant)} — ${frappe.utils.escape_html(f.reason)}</li>`)
			.join("");
		message += __("<p>Failed — please check their email address:</p><ul>{0}</ul>", [rows]);
	}

	frappe.msgprint({
		title: __("Send Personality Assessment"),
		indicator: msg.failed.length ? "orange" : "green",
		message: message,
	});
}
