"""Source blocks inserted by apply_job_field_status.py."""

FIELD_STATUS_HELPER = '''
#: What the technician's PHONE says about a job, as one value the calendar
#: can render.
#:
#: "not_dispatched" is deliberately not folded into "pending". Pending means
#: Conecto has the job and the technician has not set off yet. Not
#: dispatched means it never reached a phone at all -- nobody assigned,
#: assigned to somebody who is not a technician, or a push that failed.
#: Showing both as "Pending" would hide the one that needs somebody to act,
#: behind the one that needs nobody to do anything.
FIELD_STATUS = {
    "": "pending",
    "scheduled": "pending",
    "en_route": "en_route",
    "checked_in": "checked_in",
    "checked_out": "completed",
    "completed": "completed",
    "cancelled": "cancelled",
}


def field_status(job):
    """One of: not_dispatched, pending, en_route, checked_in, completed,
    cancelled -- or whatever Conecto sent, unrecognised.

    An unknown value passes through rather than becoming "pending": a
    status we have not seen before should appear on screen as itself, so
    somebody asks about it, rather than being quietly rounded down to the
    most reassuring thing we have.
    """
    link = getattr(job, "conecto", None)
    if link is None:
        return "not_dispatched"
    return FIELD_STATUS.get(link.last_status, link.last_status or "pending")


'''

SERIALIZER_FIELD = '''    field_status = serializers.SerializerMethodField()

    def get_field_status(self, obj):
        return field_status(obj)

'''

SERIALIZER_FIELDS_OLD = '''            "status", "start", "end", "location", "created_at", "updated_at",
'''

SERIALIZER_FIELDS_NEW = '''            "status", "field_status", "start", "end", "location",
            "created_at", "updated_at",
'''

SELECT_RELATED_OLD = '''            Job.objects.select_related("customer", "assigned_to", "ticket").all(),
'''

SELECT_RELATED_NEW = '''            # "conecto" is the reverse one-to-one the field status is read
            # from. Without it the calendar is one extra query per job,
            # which on a busy month is a hundred of them.
            Job.objects.select_related("customer", "assigned_to", "ticket", "conecto").all(),
'''

TYPES_JOB_OLD = '''  status: "scheduled" | "in_progress" | "completed" | "cancelled";
  start: string;
  end: string;
  location: string;
'''

TYPES_JOB_NEW = '''  status: "scheduled" | "in_progress" | "completed" | "cancelled";
  // What the technician's phone says, which is not the same as `status`
  // above: that one is what the office set, this one is what happened.
  // A string rather than a union because an unrecognised value from
  // Conecto is shown as itself rather than rounded to something familiar.
  field_status: string;
  start: string;
  end: string;
  location: string;
'''

STATUS_LABELS = '''
// What the technician's phone says about a job, for the calendar chip.
//
// "Not dispatched" is its own label rather than another word for Pending.
// Pending means the job is on a phone and the technician has not set off;
// not dispatched means it never got there -- nobody assigned, assigned to
// somebody who is not a technician, or a push that failed. One of those
// needs an office to do something and the other does not.
const FIELD_STATUS_LABEL: Record<string, string> = {
  not_dispatched: "Not dispatched",
  pending: "Pending",
  en_route: "En route",
  checked_in: "Checked in",
  completed: "Completed",
  cancelled: "Cancelled",
};

function fieldStatusLabel(job: Job) {
  return FIELD_STATUS_LABEL[job.field_status] ?? job.field_status.replace(/_/g, " ");
}

'''

CHIP_OLD = '''                      title={`${j.title} — ${j.customer_name ?? "standalone"} — ${jobAssignee(j)}`}
                    >
                      {new Date(j.start).toLocaleTimeString("en-ZA", { hour: "2-digit", minute: "2-digit" })} {j.title}
                      {" · "}
'''

CHIP_NEW = '''                      title={`${j.title} — ${j.customer_name ?? "standalone"} — ${jobAssignee(j)} — ${fieldStatusLabel(j)}`}
                    >
                      {new Date(j.start).toLocaleTimeString("en-ZA", { hour: "2-digit", minute: "2-digit" })} {j.title}
                      {" · "}
'''

CHIP_STATUS = '''                      {" · "}
                      {/* What the phone says, not what the office set. A
                          chip that reads Pending at four in the afternoon
                          is the one worth noticing from across the room. */}
                      <span className="opacity-90">{fieldStatusLabel(j)}</span>
'''
