"""Source blocks inserted by apply_job_cancel.py."""

# ---------------------------------------------------------------------------
# conecto/client.py
# ---------------------------------------------------------------------------

DEFAULT_URL = '''    "cancelJob": "https://africa-south1-skybre-connecto.cloudfunctions.net/cancelJob",
'''

CANCEL_JOB = '''def cancel_job(*, conecto_job_id="", vexar_job_id="", reason="", subject_ref=""):
    """Take a job off a technician's phone.

    Conecto shipped this on 2026-09-21. Before it existed, a job that was
    cancelled or moved to somebody else stayed on the original
    technician's list until they completed or ignored it -- which is a
    person driving to work that is not happening, and the single worst
    failure this integration had.

    Either identifier will do. conecto_job_id is the one they returned
    from scheduleJob and is preferred; vexar_job_id is our own primary
    key and is the fallback for a job whose id we never captured.

    Their answers: 404 for a job they do not recognise, 409 for one
    already completed or cancelled. Both are recorded in a ConectoCall row
    rather than raised, like every other call here -- a failed withdrawal
    is something to see and replay, not something to crash a save over.
    """
    if not conecto_job_id and not vexar_job_id:
        return None

    payload = {}
    if conecto_job_id:
        payload["conecto_job_id"] = conecto_job_id
    if vexar_job_id:
        payload["vexar_job_id"] = vexar_job_id
    if reason:
        payload["reason"] = reason

    return call(
        "cancelJob", payload,
        subject_kind="job", subject_ref=subject_ref or vexar_job_id,
    )


'''

# ---------------------------------------------------------------------------
# conecto/signals.py
# ---------------------------------------------------------------------------

MOVED_AWAY_OLD = '''        # Separately: Conecto has no "take this off a list" endpoint, so a
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

MOVED_AWAY_NEW = '''        # A job moved AWAY from a technician has to come off their phone,
        # or they drive to it. Until 2026-09-21 this was a warning and
        # nothing more, because Conecto had no way to withdraw one.
        if link is not None:
            _cancel_job(link, job_pk, "No longer assigned to a technician")
        return
'''

CANCELLED_OLD = '''    if job.status == Job.Status.CANCELLED:
        # Same limitation: nothing to call. Said once, here, rather than
        # pushing a cancelled job as though it were work.
        logger.warning(
            "Job %s was cancelled; Conecto has no cancel endpoint, so %s still "
            "has it on their list.", job_pk, code,
        )
        return
'''

CANCELLED_NEW = '''    if job.status == Job.Status.CANCELLED:
        # Withdraw it rather than push it. A cancelled job left on a phone
        # is somebody driving to work that is not happening.
        if link is not None:
            _cancel_job(link, job_pk, "Cancelled in Skybre")
        return
'''

REASSIGN = '''    # Moved from one technician to another. Conecto has no reassign call --
    # they were explicit about it: cancel, then schedule again for the new
    # person. Without the cancel BOTH technicians hold the job, and the one
    # who is no longer meant to do it has no way of knowing.
    #
    # Cancel first, deliberately. If the cancel fails, the new schedule
    # still goes out and the failure is a recorded call to replay -- two
    # technicians with one job, which is visible. If the order were
    # reversed and the schedule failed, the job would be on nobody's phone
    # while the calendar says it is assigned, which is not.
    if link is not None and link.technician_code and link.technician_code != code:
        _cancel_job(link, job_pk, f"Reassigned to {code}")

'''

CANCEL_FN = '''

def _cancel_job(link, job_pk, reason):
    """Withdraw one job, once.

    last_status is what stops a second attempt. Conecto answers 409 for a
    job already cancelled or completed, and a Job row gets saved by plenty
    of unrelated code -- without this, every later save of a cancelled job
    would send another cancellation and record another failure for
    something that had already worked.

    A failed withdrawal is said out loud rather than swallowed: it means a
    technician still has work on their phone that nobody intends them to
    do, and the only way anyone finds out is this line or the ConectoCall
    row behind it.
    """
    if link.last_status in ("cancelled", "completed", "checked_out"):
        return

    response = client.cancel_job(
        conecto_job_id=link.conecto_job_id,
        vexar_job_id=str(job_pk),
        reason=reason,
        subject_ref=str(job_pk),
    )
    if response is None:
        logger.warning(
            "Job %s could NOT be withdrawn from %s -- it is still on their "
            "phone and they may drive to it. See the Conecto call record and "
            "replay with conecto_resync.",
            job_pk, link.technician_code,
        )
        return

    ConectoJob.objects.filter(pk=link.pk).update(last_status="cancelled")
    link.last_status = "cancelled"
'''

# ---------------------------------------------------------------------------
# conecto/views.py
# ---------------------------------------------------------------------------

EVENT_CODE_OLD = '''            technician_code=str(self._first(data, "technician_code", "technicianCode") or ""),
'''

EVENT_CODE_NEW = '''            # Conecto sends both now. technician_code is null for anyone
            # without a hand-set code in their admin screen -- which is
            # everybody who registered with a staff code -- so whichever
            # is populated is the identity that did the work.
            technician_code=str(
                self._first(
                    data, "technician_code", "technicianCode", "staff_code", "staffCode",
                ) or ""
            ),
'''

CANCELLED_INBOUND = '''        if mapped == Job.Status.CANCELLED:
            # A cancellation only ever originates HERE. Conecto has no
            # technician-facing cancel -- their action endpoint does start,
            # checkin and checkout and nothing else -- so a "cancelled"
            # status arriving on this webhook is confirmation of a cancelJob
            # call we made, not an instruction.
            #
            # Applying it would be wrong in exactly the case that matters: a
            # REASSIGNMENT cancels the old job before scheduling the new one,
            # and letting that confirmation set our status to cancelled would
            # cancel work that is going ahead with somebody else.
            mapped = None

'''

DEFAULTS_OLD = '''        defaults={
            "technician_code": code,
'''

DEFAULTS_NEW = '''        defaults={
            "technician_code": code,
            # Cleared when the job has just moved to a different technician.
            # last_status describes the job on the OLD technician's phone,
            # which _cancel_job above has just withdrawn and marked
            # "cancelled". Left as it was, a later genuine cancellation
            # would be skipped as already done -- and the NEW technician
            # would keep work nobody intends them to do, which is the exact
            # failure this whole package exists to end.
            **({"last_status": ""} if link is not None and link.technician_code != code else {}),
'''
