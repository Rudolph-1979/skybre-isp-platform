"""FNB Fleetcard statement import (#201, Rudolph 19 Oct 2026: "Fleetcard fuel
fill ups from FNB" -> "Lets build this").

FNB's monthly Fleetcard file (e.g. 84389.txt) is one record per line, fields
separated by the letter "e" (FNB's own choice; every text field is upper
case), numbers in COBOL "overpunch" form: the last character carries the
last digit and the sign -- "{" = +0, "A".."I" = +1..+9, "}" = -0, "J".."R"
= -1..-9.

Only the fill-up records are used: record type 01, sub-type 03, one per
transaction:

    01 e 03 e CSD781 e <client> e <cost code> e <registration> e <seq> e <cdv>
       e <date YYYYMMDD> e <odometer typed at the pump> e 000000
       e E0<HHMMSS> e <garage name + area (40)><merchant no (8)> e .. e ..
       e <amount, 2 decimals> e <litres, 2 decimals> e ...

Worked out from Rudolph's September 2026 file and checked on every line
against the file's own "km since the last fill-up" and consumption figures
(and the price per litre, R25-R33). Product codes and the other fields are
not used -- we don't know their meaning and the import doesn't need them.

Each fill-up becomes a FuelLog (source "fleetcard", keyed on fleetcard_ref
so a file imported twice adds nothing) and the pump odometer goes into the
vehicle's odometer history where it fits. A fill-up already captured by
hand or from Conecto (same vehicle, same day, litres within 0.5 L) is not
added again.
"""
import datetime
import re
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import FuelLog, Vehicle

SOURCE = "fleetcard"
SAME_FILL_LITRES = Decimal("0.5")
_DIGIT = {"{": 0, "}": 0}
_DIGIT.update({c: i + 1 for i, c in enumerate("ABCDEFGHI")})
_DIGIT.update({c: i + 1 for i, c in enumerate("JKLMNOPQR")})
_NEGATIVE = set("}JKLMNOPQR")


class FleetcardError(Exception):
    pass


def overpunch(text, decimals=2):
    """'0000010000{' -> Decimal('1000.00'); '0000010000}' -> -1000.00."""
    s = (text or "").strip()
    if not s or not re.fullmatch(r"\d*[\d{}A-R]", s):
        raise ValueError(f"not a number: {text!r}")
    last = s[-1]
    if last.isdigit():
        digits, negative = s, False
    else:
        digits, negative = s[:-1] + str(_DIGIT[last]), last in _NEGATIVE
    value = Decimal(int(digits)).scaleb(-decimals)
    return -value if negative else value


def reg_key(text):
    return "".join(ch for ch in str(text or "").upper() if ch.isalnum())


def parse(text):
    """{"client", "statement_date", "fills": [...], "errors": [...]}."""
    client, statement_date, fills, errors = "", None, [], []
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip("\r\n")
        if not line.strip():
            continue
        p = line.split("e")
        if len(p) < 2:
            continue
        if p[0] == "01" and p[1] == "01":
            client = p[3].strip() if len(p) > 3 else ""
            for field in reversed(p):
                if re.fullmatch(r"20\d{6}", field.strip()):
                    try:
                        statement_date = datetime.datetime.strptime(field.strip(), "%Y%m%d").date()
                        break
                    except ValueError:
                        pass
            continue
        if not (p[0] == "01" and p[1] == "03"):
            continue
        try:
            if len(p) < 17:
                raise ValueError("too few fields")
            reg = p[5].strip()
            date = datetime.datetime.strptime(p[8].strip(), "%Y%m%d").date()
            if not p[9].strip().isdigit():
                raise ValueError("odometer")
            odometer = int(p[9].strip())
            m = re.fullmatch(r"E0(\d{2})(\d{2})(\d{2})", p[11].strip())
            if not m:
                raise ValueError("time")
            at_time = datetime.time(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            garage_field = p[12]
            merchant_no = garage_field[-8:].strip()
            garage = " ".join(garage_field[:-8].split())
            amount = overpunch(p[15])
            litres = overpunch(p[16])
            if not reg:
                raise ValueError("registration")
        except (ValueError, KeyError, IndexError) as exc:
            errors.append(f"Line {n}: not understood ({exc}) — left out.")
            continue
        fills.append({
            "line": n,
            "client": p[3].strip(),
            "registration": reg,
            "date": date,
            "time": at_time,
            "odometer_km": odometer,
            "garage": garage[:120],
            "merchant_no": merchant_no,
            "amount": amount,
            "litres": litres,
            "ref": f"{p[3].strip()}:{reg_key(reg)}:{date:%Y%m%d}{at_time:%H%M%S}:{merchant_no}"[:64],
        })
    if not fills and not errors and not client:
        raise FleetcardError("This doesn't look like an FNB Fleetcard statement file (no 01/03 fill-up lines).")
    return {"client": client, "statement_date": statement_date, "fills": fills, "errors": errors}


def plan(parsed):
    """What importing would do with each fill-up, without changing anything."""
    vehicles = {reg_key(v.registration_number): v for v in Vehicle.objects.all()}
    rows = []
    for f in parsed["fills"]:
        vehicle = vehicles.get(reg_key(f["registration"]))
        row = {**f, "vehicle": vehicle, "status": "new", "note": ""}
        if f["amount"] < 0 or f["litres"] <= 0:
            row["status"], row["note"] = "reversal", "A reversal or refund — not imported; check it with FNB."
        elif vehicle is None:
            row["status"], row["note"] = "no_vehicle", f"No vehicle {f['registration']} in Vexar — add it, then import again."
        elif FuelLog.objects.filter(fleetcard_ref=f["ref"]).exists():
            row["status"], row["note"] = "imported", "Already imported."
        else:
            twin = (
                vehicle.fuel_logs.filter(filled_at=f["date"])
                .exclude(source=SOURCE)
                .filter(litres__gte=f["litres"] - SAME_FILL_LITRES, litres__lte=f["litres"] + SAME_FILL_LITRES)
                .first()
            )
            if twin is not None:
                who = "the Conecto app" if twin.source == FuelLog.Source.CONECTO else "hand"
                row["status"], row["note"] = "captured", f"Already captured by {who} ({twin.litres} L) — not added again."
        rows.append(row)
    return rows


@transaction.atomic
def commit(rows, user):
    """Create the FuelLogs for the "new" rows. Returns (created, readings)."""
    from .conecto_logs import add_reading

    created = readings = 0
    tz = timezone.get_current_timezone()
    for row in rows:
        if row["status"] != "new":
            continue
        vehicle = row["vehicle"]
        try:
            with transaction.atomic():
                FuelLog.objects.create(
                    vehicle=vehicle,
                    filled_at=row["date"],
                    litres=row["litres"],
                    cost=row["amount"],
                    odometer_km=row["odometer_km"],
                    notes=f"FNB Fleetcard {row['time']:%H:%M}",
                    logged_by=user,
                    source=SOURCE,
                    station=row["garage"],
                    fleetcard_ref=row["ref"],
                    conecto_data={"fleetcard": {"merchant_no": row["merchant_no"], "time": row["time"].strftime("%H:%M:%S"),
                                                "client": row["client"]}},
                )
        except IntegrityError:
            row["status"], row["note"] = "imported", "Already imported."
            continue
        created += 1
        at = timezone.make_aware(datetime.datetime.combine(row["date"], row["time"]), tz)
        before = vehicle.odometer_readings.count()
        add_reading(vehicle, row["odometer_km"], at, None, row["garage"][:255], source=SOURCE)
        readings += vehicle.odometer_readings.count() - before
        row["status"] = "added"
    return created, readings


def row_json(row):
    v = row["vehicle"]
    return {
        "line": row["line"],
        "registration": row["registration"],
        "vehicle_id": v.pk if v else None,
        "vehicle_name": f"{v.make} {v.model}".strip() if v else "",
        "date": row["date"].isoformat(),
        "time": row["time"].strftime("%H:%M"),
        "garage": row["garage"],
        "litres": str(row["litres"]),
        "amount": str(row["amount"]),
        "odometer_km": row["odometer_km"],
        "status": row["status"],
        "note": row["note"],
    }
