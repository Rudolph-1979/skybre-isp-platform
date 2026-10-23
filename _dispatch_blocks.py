"""Source blocks inserted by apply_job_dispatch_guard.py."""

# ---------------------------------------------------------------------------
# conecto/signals.py
# ---------------------------------------------------------------------------

SIGNALS_IMPORT = '''from django.conf import settings
'''

CODE_FN_OLD = '''def _technician_code(user):
    link = getattr(user, "conecto_technician", None)
    if link is None or not link.is_active:
        return ""
    return link.technician_code
'''

CODE_FN_NEW = '''def _dispatch_code(user):
    """The code Conecto can actually route a job to, and which kind it is.

    Returns ("", "") when there is no way to reach this person.

    WHY THE ROLE CHECK IS NOT TIDINESS (confirmed with Conecto 2026-09-21).

    scheduleJob does not check role. It accepts the job, answers success,
    and stores it. But getting a job onto a phone needs a car-session
    token, and that is refused to anyone whose role is not `technician` --
    in their Cloud Function and again in the app itself. So a job assigned
    to an admin or an office account is created, acknowledged, recorded
    here as pushed, and then seen by nobody. Forever. With no error
    anywhere on either side.

    Refusing here is the only place that failure can be made visible, and
    a 200 from scheduleJob is not evidence that a technician got the work.

    THE TWO KINDS OF CODE.

    `technicianCode` is set by hand, per person, in Conecto's admin
    screen, and is what scheduleJob matches on today. `staff_code` is what
    this platform issues and what Conecto gates registration on -- and
    what scheduleJob will accept once Conecto ships it.

    Until then a staff code is NOT routable: sending one would create a
    job attached to nobody, which is the silent failure above wearing a
    different hat. So CONECTO_DISPATCH_BY_STAFF_CODE stays off and only a
    hand-set link counts. Turning it on afterwards is one line in the
    environment, not a deploy.
    """
    if user is None:
        return "", ""

    if user.role != user.Role.TECHNICIAN:
        return "", ""

    link = getattr(user, "conecto_technician", None)
    if link is not None and link.is_active:
        return link.technician_code, "technician"

    if getattr(settings, "CONECTO_DISPATCH_BY_STAFF_CODE", False):
        return (user.staff_code or ""), "staff"

    return "", ""
'''

CALL_SITE_OLD = '''    code = _technician_code(job.assigned_to)
'''

CALL_SITE_NEW = '''    code, code_kind = _dispatch_code(job.assigned_to)
'''

NOT_DISPATCHED_OLD = '''    if not code:
        # Unassigned, or assigned to somebody who does not use the app.
        # Conecto has no "take this off a list" endpoint, so a job moved
        # away from a technician cannot be withdrawn from their phone --
        # worth knowing, and worth saying out loud rather than failing
        # quietly, because the technician will still see it.
        if link is not None:
            logger.warning(
                "Job %s is no longer assigned to a Conecto technician (was %s). "
                "Conecto has no endpoint to withdraw it, so it stays on that "
                "technician's list until they complete or ignore it.",
                job_pk, link.technician_code,
            )
        return
'''

NOT_DISPATCHED_NEW = '''    if not code:
        # Say WHICH of these it is. "Not dispatched" covers three quite
        # different situations and only one of them is ordinary.
        assignee = job.assigned_to
        if assignee is None:
            pass  # Unassigned. Ordinary, and not worth a line in the log.
        elif assignee.role != assignee.Role.TECHNICIAN:
            # The dangerous one. Conecto would ACCEPT this job and answer
            # success, and it would reach nobody, because the car session
            # a phone needs to show it is refused to every role but
            # technician. Not sending it is the only honest outcome.
            logger.warning(
                "Job %s is assigned to %s, whose role is %r. Conecto cannot "
                "deliver a job to anyone but a technician, so it has NOT been "
                "sent -- it exists in Skybre only. Assign it to a technician.",
                job_pk, assignee.username, assignee.role,
            )
        else:
            logger.warning(
                "Job %s is assigned to %s, a technician with no code Conecto "
                "can route to. It has NOT been sent. Either link a technician "
                "code, or turn on CONECTO_DISPATCH_BY_STAFF_CODE once Conecto "
                "supports staff codes on scheduleJob.",
                job_pk, assignee.username,
            )

        # Separately: Conecto has no "take this off a list" endpoint, so a
        # job moved AWAY from a technician cannot be withdrawn from their
        # phone. Worth saying out loud rather than failing quietly,
        # because the technician will still see it.
        if link is not None:
            logger.warning(
                "Job %s is no longer assigned to a Conecto technician (was %s). "
                "Conecto has no endpoint to withdraw it, so it stays on that "
                "technician's list until they complete or ignore it.",
                job_pk, link.technician_code,
            )
        return
'''

SEND_OLD = '''    response = client.schedule_job(job, code)
'''

SEND_NEW = '''    response = client.schedule_job(
        job,
        technician_code=code if code_kind == "technician" else "",
        staff_code=code if code_kind == "staff" else "",
    )
'''

# ---------------------------------------------------------------------------
# conecto/client.py
# ---------------------------------------------------------------------------

SIGNATURE_OLD = '''def schedule_job(job, technician_code):
'''

SIGNATURE_NEW = '''def schedule_job(job, technician_code="", staff_code=""):
'''

PAYLOAD_OLD = '''    payload = {
        "technician_code": technician_code,
        "vexar_job_id": str(job.pk),
'''

PAYLOAD_NEW = '''    # One of the two, never neither. Conecto matches technician_code
    # against a field their admin sets by hand, and (once they ship it)
    # staff_code against the code this platform issues. Sending a blank
    # one alongside a real one would be a value they have to decide how to
    # interpret, so only the one being used goes.
    if not technician_code and not staff_code:
        logger.warning(
            "Refusing to schedule job %s with no technician or staff code -- "
            "Conecto would create it and attach it to nobody.", job.pk,
        )
        return None

    payload = {
        "vexar_job_id": str(job.pk),
'''

PAYLOAD_CODES = '''    if technician_code:
        payload["technician_code"] = technician_code
    if staff_code:
        payload["staff_code"] = staff_code
'''
