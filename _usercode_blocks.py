"""Source blocks inserted by apply_staff_code_entry.py."""

# ---------------------------------------------------------------------------
# accounts/serializers.py
# ---------------------------------------------------------------------------

SERIALIZER_FIELD = '''
    # Typed by the admin creating the account, not generated. Optional at
    # this level and required in validate() below, for the same reason the
    # password is: declaring it required here would also demand it on
    # every PATCH, and the Users screen patches `is_active` on its own
    # when somebody suspends or reactivates an account.
    #
    # The model still assigns one when this is blank. That path is for
    # createsuperuser, management commands and tests -- everything that
    # creates a staff account without going near this form, none of which
    # should start failing because a form field became mandatory.
    staff_code = serializers.CharField(required=False, max_length=20)
'''

SERIALIZER_READONLY_OLD = '''        # staff_code is assigned by the model, never chosen by an admin --
        # it is what Conecto gates app registration on, so a hand-edited
        # one would either collide with somebody else's or register nobody.
        read_only_fields = ["id", "staff_code"]
'''

SERIALIZER_READONLY_NEW = '''        read_only_fields = ["id"]
'''

SERIALIZER_VALIDATOR = '''
    def validate_staff_code(self, value):
        """Normalise, then insist it is nobody else's.

        Upper-cased and stripped because a code is compared, never read
        for meaning: "stf-000015" and "STF-000015" are the same code to
        every person who looks at them and two different codes to a
        database, and the day those diverge is the day one staff member
        cannot register and nobody can see why.

        The model's unique constraint would catch a collision too, but as
        a 500 from a database error rather than a message against the
        field. This is the same check, said in a place the admin can see.
        """
        value = (value or "").strip().upper()
        if not value:
            raise serializers.ValidationError("A staff code is required.")

        clash = User.objects.filter(staff_code=value)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(
                f"{value} is already used by another staff member."
            )
        return value

'''

SERIALIZER_REQUIRED = '''        # Required when creating, because Conecto gates app registration on
        # it and an account made without one simply cannot be used there --
        # a failure that surfaces days later, to the person who cannot log
        # in, rather than here where it can be fixed in a second.
        if self.instance is None and not attrs.get("staff_code"):
            raise serializers.ValidationError({
                "staff_code": "A staff code is required — the Conecto app checks it before letting them register."
            })

'''

# ---------------------------------------------------------------------------
# conecto/client.py
# ---------------------------------------------------------------------------

CLIENT_OLD = '''def register_staff_code(user):'''

CLIENT_NEW = '''def register_staff_code(user, *, code=None, is_active=None):'''

CLIENT_BODY_OLD = '''    if not user.staff_code:
        return None

    payload = {
        "staff_code": user.staff_code,
        "role": staff_role_to_conecto(user.role),
        "first_name": user.first_name or "",
        "last_name": user.last_name or "",
        "email": user.email or "",
        "is_active": bool(user.is_active and user.is_staff_member),
    }
    return call(
        "registerStaffCode", payload,
        subject_kind="staff", subject_ref=user.staff_code,
    )
'''

CLIENT_BODY_NEW = '''    # `code` overrides the one on the row, which is how a code that has
    # been CHANGED gets revoked: the row already holds the new one, and
    # the call that has to go out is about the old one.
    code = code or user.staff_code
    if not code:
        return None

    payload = {
        "staff_code": code,
        "role": staff_role_to_conecto(user.role),
        "first_name": user.first_name or "",
        "last_name": user.last_name or "",
        "email": user.email or "",
        "is_active": (
            bool(user.is_active and user.is_staff_member)
            if is_active is None else bool(is_active)
        ),
    }
    return call(
        "registerStaffCode", payload,
        subject_kind="staff", subject_ref=code,
    )
'''

# ---------------------------------------------------------------------------
# conecto/signals.py
# ---------------------------------------------------------------------------

SIGNALS_PUSH_OLD = '''    transaction.on_commit(lambda: _send_staff_code(instance.pk))
'''

SIGNALS_PUSH_NEW = '''    # A code that CHANGED has to be revoked before the replacement is
    # registered, and in that order. Conecto gates registration on the
    # code, so leaving the old one live means it holds two valid codes for
    # one person -- and the stale half is the dangerous one, because
    # nothing in this platform refers to it any more, so nothing here will
    # ever revoke it again.
    #
    # Queued as its own on_commit callback ahead of the registration:
    # callbacks run in the order they were added, and if the revocation
    # fails its ConectoCall row is the record that conecto_resync replays
    # from.
    old_code = (before or {}).get("staff_code") or ""
    if old_code and old_code != instance.staff_code:
        transaction.on_commit(
            lambda pk=instance.pk, code=old_code: _revoke_staff_code(pk, code)
        )

    transaction.on_commit(lambda: _send_staff_code(instance.pk))
'''

SIGNALS_REVOKE = '''

def _revoke_staff_code(user_pk, code):
    """Tell Conecto a code this staff member NO LONGER HAS is invalid.

    Only ever called for a code that has been replaced. Anyone who had
    already registered in the app on the old code loses access and has to
    register again with the new one -- which is the cost of letting a code
    be changed at all, and the reason changing one should be rare.
    """
    user = User.objects.filter(pk=user_pk).first()
    if user is None:
        return
    client.register_staff_code(user, code=code, is_active=False)
'''

# ---------------------------------------------------------------------------
# frontend: types/index.ts
# ---------------------------------------------------------------------------

TYPES_OLD = '''export interface StaffAccountEntry {
  id: number;
  username: string;
'''

TYPES_NEW = '''export interface StaffAccountEntry {
  id: number;
  username: string;
  // What the Conecto app checks before letting this person register.
  // Nullable because customers share this model and have none, and
  // because accounts created before staff codes existed were backfilled
  // rather than invented.
  staff_code: string | null;
'''

# ---------------------------------------------------------------------------
# frontend: pages/admin/ConfigsPage.tsx
# ---------------------------------------------------------------------------

FORM_EMPTY_OLD = '''const EMPTY_USER_FORM = {
  username: "",
'''

FORM_EMPTY_NEW = '''const EMPTY_USER_FORM = {
  username: "",
  staff_code: "",
'''

FORM_EDIT_OLD = '''    setForm({
      username: entry.username,
      password: "",
'''

FORM_EDIT_NEW = '''    setForm({
      username: entry.username,
      staff_code: entry.staff_code ?? "",
      password: "",
'''

FORM_PAYLOAD_OLD = '''      const payload: Record<string, unknown> = {
        username: form.username,
'''

FORM_PAYLOAD_NEW = '''      const payload: Record<string, unknown> = {
        username: form.username,
        staff_code: form.staff_code,
'''

FORM_FIELD = '''            <FormField label="Staff code">
              <input
                className={inputClass}
                required
                value={form.staff_code}
                onChange={(e) => setForm({ ...form, staff_code: e.target.value })}
                placeholder="STF-000015"
                autoComplete="off"
                name="staff-code"
              />
            </FormField>
            <p className="-mt-2 mb-3 text-xs text-[var(--text-muted)]">
              {editing
                ? "The Conecto app checks this before letting them register. Changing it revokes the old code — anyone already registered on it will have to register again with the new one."
                : "The Conecto app checks this before letting them register. Must be unique across all staff."}
            </p>
'''
