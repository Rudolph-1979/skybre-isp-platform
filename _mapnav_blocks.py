"""Source blocks inserted by apply_positions_map.py."""

# ---------------------------------------------------------------------------
# conecto/views.py
# ---------------------------------------------------------------------------

VIEWS_IMPORTS = '''from datetime import timedelta

from rest_framework import permissions
'''

VIEWS_PERM_IMPORT = '''from accounts.permissions import IsStaffMember, section_permission
'''

POSITIONS_VIEW = '''

HasSchedulingAccess = section_permission("scheduling")

#: How far back a position is worth drawing. Somebody who last reported
#: yesterday afternoon is not anywhere useful on today's map -- the pin
#: would look exactly like a current one, distinguished only by a timestamp
#: nobody reads twice.
POSITION_WINDOW_HOURS = 12


def _position_row(code, user, event):
    name = ""
    if user is not None:
        name = (user.first_name or "").strip() or user.username

    minutes_ago = None
    if event is not None and event.occurred_at is not None:
        minutes_ago = int((timezone.now() - event.occurred_at).total_seconds() // 60)

    job = getattr(event, "job", None) if event is not None else None
    return {
        "code": code or (user.staff_code if user is not None else ""),
        "name": name or code or "Unknown",
        "status": event.status if event is not None else "",
        "lat": float(event.lat) if event is not None and event.lat is not None else None,
        "lng": float(event.lng) if event is not None and event.lng is not None else None,
        "occurred_at": (
            event.occurred_at.isoformat()
            if event is not None and event.occurred_at is not None else None
        ),
        "minutes_ago": minutes_ago,
        "job_id": job.pk if job is not None else None,
        "job_title": job.title if job is not None else "",
        "customer_name": (
            job.customer.full_name if job is not None and job.customer_id else ""
        ),
    }


class TechnicianPositionsView(APIView):
    """Where each technician was last seen, and how long ago.

    THIS IS NOT LIVE TRACKING, and the screen showing it says so in as
    many words.

    Conecto confirmed on 2026-09-21 that a position reaches us only with a
    STATUS CHANGE, and only twice per job: where the technician tapped
    start, and the site they checked in at. The continuous tracking their
    app runs while en route never leaves the phone -- it exists to compute
    kilometres and the intermediate points are discarded on the device.

    So a pin here can be an hour old and still be the newest thing that
    exists anywhere, and the checked-in one is usually the job's own
    address. The occurred_at beside each is not decoration; it is the
    difference between "he is there" and "he was there before lunch".
    This becomes a genuine live map the day Conecto add periodic
    reporting, with nothing here needing to change.

    Technicians who have reported nothing are returned anyway, with nulls.
    Leaving them out would let a map read as "nothing to report" when what
    it means is "we do not know where this person is", and those are
    different sentences.
    """

    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasSchedulingAccess]

    def get(self, request):
        from django.contrib.auth import get_user_model

        from .models import ConectoJobEvent, ConectoTechnician

        User = get_user_model()

        technicians = list(
            User.objects.filter(role=User.Role.TECHNICIAN, is_active=True)
            .order_by("first_name", "username")
        )

        # A technician is known to Conecto by a staff code, or by a
        # hand-set technician code, or historically by both. An event
        # carries whichever one that phone registered under, so the lookup
        # has to work from either.
        code_to_user = {}
        for user in technicians:
            if user.staff_code:
                code_to_user[user.staff_code] = user
        for link in ConectoTechnician.objects.select_related("user"):
            if link.technician_code:
                code_to_user[link.technician_code] = link.user

        since = timezone.now() - timedelta(hours=POSITION_WINDOW_HOURS)
        latest = {}
        events = (
            ConectoJobEvent.objects
            .filter(occurred_at__gte=since)
            .exclude(technician_code="")
            .select_related("job", "job__customer")
            .order_by("-occurred_at", "-id")
        )
        for event in events:
            # Newest first, so the first one seen for a code is the one to
            # keep. Cheaper than a window function and easier to read.
            latest.setdefault(event.technician_code, event)

        rows = []
        seen = set()
        for code, event in latest.items():
            user = code_to_user.get(code)
            if user is not None:
                seen.add(user.pk)
            rows.append(_position_row(code, user, event))

        for user in technicians:
            if user.pk not in seen:
                rows.append(_position_row(user.staff_code or "", user, None))

        # Anybody with a position first, then the unaccounted-for, each
        # group by name. A technician nobody has heard from belongs at the
        # bottom of the list, not scattered through it.
        rows.sort(key=lambda row: (row["minutes_ago"] is None, row["name"].lower()))
        return Response(rows)
'''

# ---------------------------------------------------------------------------
# conecto/urls.py
# ---------------------------------------------------------------------------

URLS_DOC_OLD = '''them exactly as written above.
"""
'''

URLS_DOC_NEW = '''them exactly as written above.

One read-only endpoint sits alongside those, for the Scheduling screens:

    /api/technician-positions/
"""
'''

URLS_IMPORT = '''    TechnicianPositionsView,
'''

URLS_PATH = '''    path("technician-positions/", TechnicianPositionsView.as_view()),
'''

# ---------------------------------------------------------------------------
# frontend: types/index.ts
# ---------------------------------------------------------------------------

TYPES_POSITION = '''

export interface TechnicianPosition {
  code: string;
  name: string;
  status: string;
  // Null for a technician whose phone has reported nothing. They are still
  // in the list, because "we do not know where this person is" and
  // "nothing to report" are different sentences.
  lat: number | null;
  lng: number | null;
  occurred_at: string | null;
  minutes_ago: number | null;
  job_id: number | null;
  job_title: string;
  customer_name: string;
}
'''

# ---------------------------------------------------------------------------
# frontend: components/AdminLayout.tsx
# ---------------------------------------------------------------------------

NAV_OLD = '''  { to: "/admin/scheduling", label: "Scheduling", section: "scheduling" },
'''

NAV_NEW = '''  {
    to: "/admin/scheduling",
    label: "Scheduling",
    section: "scheduling",
    children: [
      // Named for what it is. During a quiet hour the last reported
      // position and "where they are" are the same thing; during a drive
      // between towns they are not, and the label should not promise the
      // second one.
      { to: "/admin/scheduling/positions", label: "Technician positions", section: "scheduling" },
    ],
  },
'''

# ---------------------------------------------------------------------------
# frontend: App.tsx
# ---------------------------------------------------------------------------

APP_IMPORT = '''import { TechnicianPositionsPage } from "./pages/admin/TechnicianPositionsPage";
'''

APP_ROUTE = '''            <Route path="scheduling/positions" element={<SectionRoute section="scheduling"><TechnicianPositionsPage /></SectionRoute>} />
'''

# ---------------------------------------------------------------------------
# frontend: pages/admin/SchedulingPage.tsx
# ---------------------------------------------------------------------------

PAGE_IMPORT = '''import { usePolling } from "../../hooks/usePolling";
'''

JOBS_HOOK_OLD = '''  const { items: jobs, loading: jobsLoading, refetch: refetchJobs } = useApiList<Job>(
'''

JOBS_HOOK_NEW = '''  const {
    items: jobs, loading: jobsLoading, refetch: refetchJobs,
    refetchQuietly: refetchJobsQuietly,
  } = useApiList<Job>(
'''

SHIFTS_HOOK_OLD = '''  const { items: shifts, loading: shiftsLoading, refetch: refetchShifts } = useApiList<Shift>(
'''

SHIFTS_HOOK_NEW = '''  const {
    items: shifts, loading: shiftsLoading, refetch: refetchShifts,
    refetchQuietly: refetchShiftsQuietly,
  } = useApiList<Shift>(
'''

PAGE_POLL = '''  // A job's status changes on a technician's phone, not in this browser.
  // The calendar is where the office looks to see whether somebody has set
  // off, so it has to move on its own or it is only ever as current as the
  // last time anybody pressed refresh.
  //
  // Quiet, and only while the tab is in front -- a calendar left open on a
  // spare monitor all day should cost two requests a minute and nothing
  // when the screen is locked. An inline arrow is fine here: usePolling
  // holds the callback in a ref, so this does not rebuild the timer.
  usePolling(() => {
    refetchJobsQuietly();
    refetchShiftsQuietly();
  }, 30_000);

'''
