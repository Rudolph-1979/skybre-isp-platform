import random
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction

from accounts.models import User
from customers.models import Customer
from billing.models import Tariff, Service, Invoice, InvoiceItem, Payment
from network.models import Device, IPPool, IPAddress
from tickets.models import Ticket, TicketComment


FIRST_NAMES = ["Thabo", "Sipho", "Naledi", "Aisha", "John", "Mary", "Pieter", "Zanele", "David", "Fatima",
               "Michael", "Lerato", "James", "Nomvula", "Chris", "Amahle", "Kevin", "Precious", "Andre", "Busisiwe"]
LAST_NAMES = ["Nkosi", "Van der Merwe", "Dlamini", "Mokoena", "Botha", "Khumalo", "Smith", "Naidoo", "Mahlangu",
              "Fourie", "Zulu", "Pretorius", "Sithole", "Adams", "Mnguni", "Coetzee", "Radebe", "Joubert"]
CITIES = ["Johannesburg", "Pretoria", "Cape Town", "Durban", "Bloemfontein", "Polokwane", "Nelspruit"]


class Command(BaseCommand):
    help = "Populate the database with realistic demo data for the ISP platform."

    def add_arguments(self, parser):
        parser.add_argument("--flush", action="store_true", help="Delete existing demo data first.")

    @transaction.atomic
    def handle(self, *args, **options):
        if options["flush"]:
            self.stdout.write("Flushing existing data...")
            TicketComment.objects.all().delete()
            Ticket.objects.all().delete()
            Payment.objects.all().delete()
            InvoiceItem.objects.all().delete()
            Invoice.objects.all().delete()
            IPAddress.objects.all().delete()
            IPPool.objects.all().delete()
            Service.objects.all().delete()
            Device.objects.all().delete()
            Tariff.objects.all().delete()
            Customer.objects.all().delete()
            User.objects.exclude(is_superuser=True).delete()

        self.stdout.write("Creating staff users...")
        admin, _ = User.objects.get_or_create(
            username="admin", defaults={"email": "admin@isp.local", "role": User.Role.ADMIN,
                                         "is_staff": True, "is_superuser": True, "first_name": "Platform", "last_name": "Admin"}
        )
        admin.set_password("admin12345")
        admin.save()

        staff_members = []
        for uname, first, role in [
            ("jsupport", "Jordan", User.Role.SUPPORT),
            ("nbilling", "Naledi", User.Role.ACCOUNTS),
            ("ttech", "Thabo", User.Role.TECHNICIAN),
            ("ssales", "Sipho", User.Role.SALES),
            ("mmanage", "Mpho", User.Role.MANAGEMENT),
        ]:
            u, _ = User.objects.get_or_create(
                username=uname, defaults={"email": f"{uname}@isp.local", "role": role, "is_staff": True,
                                           "first_name": first, "last_name": "Team"}
            )
            u.set_password("staff12345")
            u.save()
            staff_members.append(u)

        self.stdout.write("Creating tariffs...")
        tariff_defs = [
            ("Home Fibre 20Mbps", 20, 5, 399, None),
            ("Home Fibre 50Mbps", 50, 25, 599, None),
            ("Home Fibre 100Mbps", 100, 50, 799, None),
            ("Business Fibre 200Mbps", 200, 100, 1899, None),
            ("LTE Uncapped 10Mbps", 10, 5, 499, 200),
            ("Voice Bundle 500min", 0, 0, 149, None),
        ]
        tariffs = []
        for name, down, up, price, cap in tariff_defs:
            t, _ = Tariff.objects.get_or_create(
                name=name,
                defaults=dict(
                    service_type=Tariff.ServiceType.VOICE if "Voice" in name else Tariff.ServiceType.INTERNET,
                    price=price, billing_period=Tariff.BillingPeriod.MONTHLY,
                    speed_download_kbps=down or None, speed_upload_kbps=up or None,
                    data_cap_gb=cap, tax_rate_pct=15, is_active=True,
                    description=f"{name} package.",
                ),
            )
            tariffs.append(t)

        self.stdout.write("Creating network devices & IP pools...")
        devices = []
        device_defs = [
            ("Core Router - JHB", Device.DeviceType.ROUTER, "10.0.0.1", "Johannesburg DC"),
            ("Core Router - CPT", Device.DeviceType.ROUTER, "10.0.1.1", "Cape Town DC"),
            ("OLT-01", Device.DeviceType.OLT, "10.0.0.10", "Johannesburg DC"),
            ("Switch-Access-01", Device.DeviceType.SWITCH, "10.0.0.20", "Sandton POP"),
            ("AP-Rooftop-01", Device.DeviceType.AP, "10.0.2.5", "Rooftop Site A"),
            ("Radius Server", Device.DeviceType.SERVER, "10.0.0.5", "Johannesburg DC"),
        ]
        for name, dtype, ip, loc in device_defs:
            d, _ = Device.objects.get_or_create(
                name=name, defaults=dict(device_type=dtype, ip_address=ip, location=loc,
                                          vendor="MikroTik", model_name="CCR2004", status=Device.Status.ONLINE)
            )
            devices.append(d)

        pool, _ = IPPool.objects.get_or_create(
            name="Customer Pool A", defaults=dict(network_cidr="10.10.0.0/22", gateway="10.10.0.1")
        )
        existing_ips = set(pool.addresses.values_list("address", flat=True))
        ip_addresses = []
        for i in range(2, 60):
            addr = f"10.10.0.{i}"
            if addr in existing_ips:
                ip_addresses.append(IPAddress.objects.get(address=addr))
                continue
            ip_addresses.append(IPAddress.objects.create(pool=pool, address=addr, status=IPAddress.Status.FREE))

        self.stdout.write("Creating customers, services, invoices & payments...")
        statuses = [Customer.Status.ACTIVE] * 6 + [Customer.Status.NEW, Customer.Status.SUSPENDED, Customer.Status.INACTIVE]
        customers = []
        for i in range(40):
            first = random.choice(FIRST_NAMES)
            last = random.choice(LAST_NAMES)
            full_name = f"{first} {last}"
            is_business = random.random() < 0.2
            email = f"{first.lower()}.{last.lower().replace(' ', '')}{i}@example.com"

            customer_user = None
            if random.random() < 0.6:
                username = f"cust{i:03d}"
                customer_user, _ = User.objects.get_or_create(
                    username=username, defaults={"email": email, "role": User.Role.CUSTOMER,
                                                  "first_name": first, "last_name": last}
                )
                customer_user.set_password("customer12345")
                customer_user.save()

            customer = Customer.objects.create(
                user=customer_user,
                customer_type=Customer.CustomerType.COMPANY if is_business else Customer.CustomerType.INDIVIDUAL,
                category=Customer.Category.BUSINESS if is_business else Customer.Category.RESIDENTIAL,
                full_name=full_name,
                company_name=f"{last} Holdings" if is_business else "",
                email=email,
                phone=f"08{random.randint(10000000, 99999999)}",
                address=f"{random.randint(1, 200)} Main Street",
                city=random.choice(CITIES),
                zip_code=str(random.randint(1000, 9999)),
                status=random.choice(statuses),
                assigned_staff=random.choice(staff_members),
            )
            customers.append(customer)

            tariff = random.choice(tariffs)
            service = Service.objects.create(
                customer=customer, tariff=tariff, device=random.choice(devices),
                status=Service.Status.ACTIVE if customer.status == Customer.Status.ACTIVE else Service.Status.SUSPENDED,
                start_date=timezone.now().date() - timedelta(days=random.randint(30, 700)),
            )

            free_ip = next((ip for ip in ip_addresses if ip.status == IPAddress.Status.FREE), None)
            if free_ip:
                free_ip.status = IPAddress.Status.ASSIGNED
                free_ip.assigned_service = service
                free_ip.save()

            # Create 2-4 months of invoice history per customer
            balance = Decimal("0")
            for m in range(random.randint(2, 4)):
                due_date = timezone.now().date() - timedelta(days=30 * m)
                invoice = Invoice.objects.create(customer=customer, date_due=due_date)
                InvoiceItem.objects.create(
                    invoice=invoice, service=service, description=f"{tariff.name} - Monthly Subscription",
                    quantity=1, unit_price=tariff.price, tax_rate_pct=tariff.tax_rate_pct,
                )
                invoice.recalc_totals()

                paid = random.random() < 0.75
                if paid:
                    payment = Payment.objects.create(
                        customer=customer, invoice=invoice, amount=invoice.total,
                        method=random.choice(list(Payment.Method.values)), received_by=random.choice(staff_members),
                    )
                    invoice.paid_amount = invoice.total
                    invoice.status = Invoice.Status.PAID
                    invoice.save()
                else:
                    invoice.status = Invoice.Status.OVERDUE if m > 0 else Invoice.Status.UNPAID
                    invoice.save()
                    balance += invoice.total

            customer.balance = balance
            customer.save()

            # Tickets for ~40% of customers
            if random.random() < 0.4:
                ticket = Ticket.objects.create(
                    customer=customer,
                    subject=random.choice([
                        "Internet connection keeps dropping",
                        "Billing discrepancy on last invoice",
                        "Request to upgrade package",
                        "Slow speeds during peak hours",
                        "Router not powering on",
                    ]),
                    description="Customer reported the issue via phone call.",
                    department=random.choice(list(Ticket.Department.values)),
                    status=random.choice(list(Ticket.Status.values)),
                    priority=random.choice(list(Ticket.Priority.values)),
                    assigned_to=random.choice(staff_members),
                )
                TicketComment.objects.create(
                    ticket=ticket, author=ticket.assigned_to,
                    message="Looking into this now, will update shortly.", is_internal=False,
                )

        self.stdout.write(self.style.SUCCESS(
            f"Seed complete: {len(customers)} customers, {len(tariffs)} tariffs, {len(devices)} devices, "
            f"{Invoice.objects.count()} invoices, {Payment.objects.count()} payments, {Ticket.objects.count()} tickets."
        ))
        self.stdout.write(self.style.SUCCESS(
            "Login as staff: admin/admin12345, jsupport/staff12345, nbilling/staff12345, ttech/staff12345, "
            "ssales/staff12345, mmanage/staff12345"
        ))
        self.stdout.write(self.style.SUCCESS("Customer portal logins: cust000..cust0XX / customer12345"))
