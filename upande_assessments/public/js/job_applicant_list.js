// Upande Assessments — Job Applicant list view.
//
// Adds a bulk "Send Personality Assessment" action for HR: filter the list to a
// Job Opening + ats_result = Pass, select the rows, then Actions → Send.
//
// Coexists with upande_ats's own Job Applicant list settings. upande_ats sets
// its onload via Object.assign (which does NOT chain), so we capture any
// existing onload and call it first — never clobbering the ATS customisation.
frappe.listview_settings["Job Applicant"] = frappe.listview_settings["Job Applicant"] || {};
const _prev = frappe.listview_settings["Job Applicant"].onload;
frappe.listview_settings["Job Applicant"].onload = function (lv) {
	if (_prev) _prev(lv);
	lv.page.add_action_item(__("Send Personality Assessment"), () => {
		const items = lv.get_checked_items();
		if (!items.length) {
			frappe.msgprint(__("Select at least one applicant."));
			return;
		}
		frappe.call({
			method: "upande_assessments.api.bulk_send_assessment",
			args: { applicants: items.map((d) => d.name) },
			freeze: true,
			freeze_message: __("Sending assessments…"),
			callback: (r) => show_bulk_summary(r.message),
		});
	});
};

function show_bulk_summary(msg) {
	if (!msg) return;

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
