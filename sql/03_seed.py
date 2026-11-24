#!/usr/bin/env python3
"""
RescueLink -- deterministic data seeding script
===============================================

Populates the schema from sql/01_schema.sql with ~75,000 realistic rows:
40 partner organisations, 81 users, 12,000 donation batches, 15,000+
allocations, 1,200 delivery runs, certification records, cold-chain checks
and an audit trail.

Design goals
------------
* Reproducible: a fixed RNG seed means every run produces identical data,
  so EXPLAIN ANALYZE numbers in the report stay comparable.
* Referentially honest: every allocation goes to a recipient that is
  actually *capable* of storing that food category, every run's crew has
  a valid driver licence, refrigerated cargo only travels in a refrigerated
  vehicle, and handed-over kg never exceeds allocated kg.
* Physically plausible: quantities, temperatures and shelf lives per
  category, including a realistic share of cold-chain failures and spoilage.

Usage
-----
    python sql/03_seed.py            # wipe + reseed (default)
    python sql/03_seed.py --no-wipe   # append on top of existing rows

Connection settings come from the usual PG* environment variables with
development defaults (port 5433, the project-local cluster).
"""

from __future__ import annotations

import os
import random
import sys
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.types.json import Json
from werkzeug.security import generate_password_hash

SEED = 20261210
RNG = random.Random(SEED)
NOW = datetime.now(timezone.utc).replace(microsecond=0)
DEMO_PASSWORD = "Demo1234!"

DSN = (
    f"host={os.environ.get('PGHOST', '127.0.0.1')} "
    f"port={os.environ.get('PGPORT', '5433')} "
    f"dbname={os.environ.get('PGDATABASE', 'rescuelink')} "
    f"user={os.environ.get('PGUSER', 'rescue_admin')} "
    f"password={os.environ.get('PGPASSWORD', 'rescue_dev_pw')}"
)

# ---------------------------------------------------------------------------
# Master data
# ---------------------------------------------------------------------------
DISTRICTS = [
    "Kadikoy", "Besiktas", "Sisli", "Uskudar", "Fatih", "Bakirkoy",
    "Atasehir", "Maltepe", "Beyoglu", "Zeytinburnu", "Pendik", "Kartal",
    "Beykoz", "Tuzla",
]

DONOR_NAMES = [
    ("Ekmekci Firinci", "bakery"),            # 1
    ("Unlu Mamuller Kozyatagi", "bakery"),
    ("Migros Kadikoy", "market"),
    ("CarrefourSA Maltepe", "market"),
    ("Sok Market Uskudar", "market"),
    ("Hilton Bomonti Kitchen", "hotel"),
    ("Divan Pastanesi Nisantasi", "bakery"),
    ("Kahvalti Evi Besiktas", "restaurant"),
    ("Zeytin Restaurant Beyoglu", "restaurant"),
    ("Pizza Piazza Atasehir", "restaurant"),
    ("Sutluce Catering", "caterer"),
    ("Anadolu Lisesi Cafeteria", "canteen"),
    ("Bostanci Balikcisi", "restaurant"),
    ("Manav Huseyin", "greengrocer"),
    ("Moda Coffee Roasters", "cafe"),
    ("Caddebostan Dondurma", "cafe"),
    ("Sodexo Corporate Kitchen", "caterer"),
    ("Simit Sarayi Bakirkoy", "bakery"),
    ("Organik Pazar Sisli", "market"),
    ("Otel Marmara Buffet", "hotel"),
    ("Lezzet Lokantasi Fatih", "restaurant"),
    ("Ege Sarkuteri Zeytinburnu", "deli"),
    ("Firinci Ali Pendik", "bakery"),
    ("Metro Wholesale Market", "market"),
]

RECIPIENT_NAMES = [
    ("Kadikoy Solidarity Kitchen", 1200),   # 25
    ("Student Dormitory Soup Kitchen", 800),
    ("Umut Foundation Food Bank", 3000),
    ("Besiktas Neighbourhood Kitchen", 500),
    ("Sisli Social Aid Centre", 700),
    ("Uskudar Elderly Care Home", 400),
    ("Atasehir Womens Shelter", 300),
    ("Maltepe Childrens Home", 350),
    ("Fatih Public Kitchen", 900),
    ("Bakirkoy Homeless Collective", 250),
    ("Beyoglu Food Bank", 1500),
    ("Zeytinburnu Youth Centre", 450),
    ("Pendik Refugee Support Centre", 600),
    ("Kartal Hope House", 300),
    ("Beykoz Nature Collective", 200),
    ("Tuzla Seafarers Club", 250),
]

FIRST_NAMES = [
    "Elif", "Mert", "Zeynep", "Burak", "Ayse", "Can", "Deniz", "Selin",
    "Emre", "Hande", "Kerem", "Nazli", "Onur", "Pinar", "Serkan", "Tugba",
    "Umut", "Yasemin", "Berk", "Ceren", "Derya", "Efe", "Gizem", "Hakan",
    "Ilkay", "Jale", "Kaan", "Leyla", "Murat", "Nilay", "Orhan", "Pelin",
    "Riza", "Sibel", "Tolga", "Vildan", "Yusuf", "Zehra",
]
LAST_NAMES = [
    "Yilmaz", "Kaya", "Demir", "Sahin", "Celik", "Yildiz", "Yildirim",
    "Ozturk", "Aydin", "Ozdemir", "Arslan", "Dogan", "Kilic", "Aslan",
    "Cetin", "Kara", "Koc", "Kurt", "Ozkan", "Simsek", "Erdogan",
    "Bulut", "Guven", "Tekin", "Aksoy", "Unal", "Polat", "Yalcin",
]

# category, needs_refrigeration, shelf_life_days, kg_per_meal
CATEGORIES = [
    ("Bakery & Pastry", False, 3, 0.35),
    ("Fresh Produce", True, 7, 0.40),
    ("Dairy & Eggs", True, 3, 0.45),
    ("Cooked Meals", True, 2, 0.50),
    ("Deli & Prepared Foods", True, 3, 0.40),
    ("Meat & Poultry", True, 2, 0.55),
    ("Seafood", True, 1, 0.50),
    ("Dry Goods & Pantry", False, 180, 0.30),
    ("Beverages", False, 30, 0.25),
    ("Frozen Foods", True, 90, 0.40),
    ("Confectionery", False, 14, 0.30),
    ("Baby Food & Formula", False, 60, 0.30),
]

ITEMS = {
    "Bakery & Pastry": ["Assorted sourdough loaves", "Simit and pogaca trays",
                        "Whole wheat sandwich bread", "Croissant and danish mix"],
    "Fresh Produce": ["Tomatoes and cucumbers", "Seasonal fruit mix",
                      "Leafy greens", "Bell peppers and courgettes"],
    "Dairy & Eggs": ["Yogurt tubs 500 g", "Fresh milk crates",
                     "White cheese blocks", "Free-range eggs"],
    "Cooked Meals": ["Lentil soup bulk trays", "Rice pilaf portions",
                     "Vegetable stew portions", "Baked pasta trays"],
    "Deli & Prepared Foods": ["Cold cut platters", "Prepared salad bowls",
                              "Sandwich platters"],
    "Meat & Poultry": ["Chicken breast fillets", "Minced beef",
                       "Sliced turkey breast"],
    "Seafood": ["Sea bass fillets", "Anchovy crates", "Mussels in brine"],
    "Dry Goods & Pantry": ["Rice sacks 5 kg", "Pasta cartons", "Flour bags",
                           "Sunflower oil drums"],
    "Beverages": ["Fruit juice bottles", "Bottled water", "Ayran cartons"],
    "Frozen Foods": ["Frozen vegetables", "Frozen pastry dough",
                     "Frozen fries"],
    "Confectionery": ["Chocolate bars", "Baklava trays", "Biscuit boxes"],
    "Baby Food & Formula": ["Infant formula tins", "Baby food jars"],
}

# which categories each kind of donor typically hands over
DONOR_KINDS = {
    "bakery":     ["Bakery & Pastry", "Confectionery"],
    "market":     ["Fresh Produce", "Dairy & Eggs", "Dry Goods & Pantry",
                   "Beverages", "Frozen Foods"],
    "hotel":      ["Cooked Meals", "Deli & Prepared Foods", "Bakery & Pastry",
                   "Fresh Produce"],
    "restaurant": ["Cooked Meals", "Deli & Prepared Foods", "Fresh Produce"],
    "caterer":    ["Cooked Meals", "Deli & Prepared Foods", "Bakery & Pastry"],
    "canteen":    ["Cooked Meals", "Fresh Produce", "Bakery & Pastry"],
    "greengrocer": ["Fresh Produce"],
    "cafe":       ["Bakery & Pastry", "Dairy & Eggs", "Beverages"],
    "deli":       ["Deli & Prepared Foods", "Dairy & Eggs", "Meat & Poultry"],
}

SKILLS = [
    ("Food Safety Level 1", "Mandatory hygiene certificate for handling food"),
    ("Driver Licence C1", "Licence valid for vans up to 7.5 tonnes"),
    ("Cold Chain Handling", "Trained on refrigerated transport procedures"),
    ("Heavy Lifting", "Trained for crates above 20 kg"),
    ("First Aid", "Basic first aid responder"),
    ("Forklift Operator", "Licensed forklift operator for warehouse pickups"),
]

PASSWORD_HASH = generate_password_hash(DEMO_PASSWORD, method="pbkdf2:sha256")


def slug(name: str) -> str:
    table = str.maketrans("cgiosuCGIOSU", "cgiosuCGIOSU")
    out = name.lower().translate(table)
    clean = [ch if ch.isalnum() else "-" for ch in out]
    return "".join(clean).strip("-").replace("--", "-")


def phone() -> str:
    return f"+90 5{RNG.randint(30, 39)} {RNG.randint(200, 899)} {RNG.randint(10, 99)} {RNG.randint(10, 99)}"


def person() -> str:
    return f"{RNG.choice(FIRST_NAMES)} {RNG.choice(LAST_NAMES)}"


def split_kg(total: float, parts: int) -> list[float]:
    """Split total kg into `parts` positive shares that sum exactly to total."""
    if parts == 1:
        return [round(total, 2)]
    weights = [RNG.uniform(0.6, 1.6) for _ in range(parts)]
    s = sum(weights)
    values = [round(total * w / s, 1) for w in weights]
    while min(values) < 2.0:                      # keep every share meaningful
        weights = [RNG.uniform(0.8, 1.2) for _ in range(parts)]
        s = sum(weights)
        values = [round(total * w / s, 1) for w in weights]
    values[-1] = round(total - sum(values[:-1]), 1)
    if values[-1] <= 0:
        values[-1] = 1.0
        values[0] = round(total - sum(values[1:]), 1)
    return values


def main() -> None:
    wipe = "--no-wipe" not in sys.argv
    conn = psycopg.connect(DSN)
    conn.autocommit = False
    cur = conn.cursor()

    if wipe:
        cur.execute("""
            TRUNCATE audit_log, handovers, cold_chain_checks, shift_assignments,
                     volunteer_skills, skills, run_batches, delivery_runs,
                     vehicles, batch_allocations, donation_batches,
                     org_capabilities, food_categories, users, organizations, roles
            RESTART IDENTITY CASCADE
        """)

    # -- roles ------------------------------------------------------------
    roles = [
        ("Admin", "Platform administrator: manages partners, users, taxonomy, audit"),
        ("Donor Staff", "Posts surplus batches and tracks how much was rescued"),
        ("Recipient Coordinator", "Claims batches, builds delivery runs, signs handovers"),
        ("Volunteer Courier", "Picks up and delivers batches, logs volunteer hours"),
    ]
    cur.executemany(
        "INSERT INTO roles (role_id, role_name, description) OVERRIDING SYSTEM VALUE VALUES (%s,%s,%s)",
        [(i, n, d) for i, (n, d) in enumerate(roles, start=1)],
    )
    ADMIN, DONOR, COORD, COURIER = 1, 2, 3, 4

    # -- organizations ----------------------------------------------------
    orgs = []   # (org_id, name, type, district, capacity)
    for idx, (name, _kind) in enumerate(DONOR_NAMES, start=1):
        orgs.append((idx, name, "donor", RNG.choice(DISTRICTS), 0))
    for idx, (name, cap) in enumerate(RECIPIENT_NAMES, start=25):
        orgs.append((idx, name, "recipient", RNG.choice(DISTRICTS), cap))

    cur.executemany("""
        INSERT INTO organizations (org_id, name, org_type, tax_id, address,
                                   district, contact_email, phone,
                                   cold_storage_kg, is_active)
        OVERRIDING SYSTEM VALUE
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, [
        (oid, name, otype, f"{1000000000 + oid * 7919 % 8999999999}",
         f"{RNG.randint(1, 240)} {RNG.choice(['Cumhuriyet','Bagdat','Moda','Barbaros','Rasimpasa','Halaskargazi'])} Cd. No:{RNG.randint(1, 90)}",
         district, f"contact@{slug(name)}.org", phone(), cap,
         not (oid % 17 == 0))          # a few inactive partners
        for oid, name, otype, district, cap in orgs
    ])
    donor_ids = [o[0] for o in orgs if o[2] == "donor"]
    recipient_ids = [o[0] for o in orgs if o[2] == "recipient"]

    # -- users ------------------------------------------------------------
    org_name = {o[0]: o[1] for o in orgs}
    users = []        # (id, email, name, role, org)
    users.append((1, "admin@rescuelink.org", "Deniz Yilmaz", ADMIN, None))
    next_id = 2
    for i, oid in enumerate(donor_ids):
        email = "donor@rescuelink.org" if i == 0 else f"donor{i:02d}@{slug(org_name[oid])}.org"
        users.append((next_id, email, person(), DONOR, oid))
        next_id += 1
    for i, oid in enumerate(recipient_ids):
        email = "coordinator@rescuelink.org" if i == 0 else f"coord{i:02d}@{slug(org_name[oid])}.org"
        users.append((next_id, email, person(), COORD, oid))
        next_id += 1
    courier_ids = []
    for i in range(40):
        oid = RNG.choice(recipient_ids)
        email = "courier@rescuelink.org" if i == 0 else f"courier{i:02d}@rescuelink.org"
        users.append((next_id, email, person(), COURIER, oid))
        courier_ids.append(next_id)
        next_id += 1
    coord_ids = [u[0] for u in users if u[3] == COORD]

    cur.executemany("""
        INSERT INTO users (user_id, email, password_hash, full_name, phone,
                           role_id, org_id, is_active, created_at)
        OVERRIDING SYSTEM VALUE
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, [
        (uid, email, PASSWORD_HASH, name, phone(), role, org,
         True, NOW - timedelta(days=RNG.randint(30, 400)))
        for uid, email, name, role, org in users
    ])

    # -- food categories --------------------------------------------------
    cur.executemany("""
        INSERT INTO food_categories (category_id, name, needs_refrigeration,
                                     shelf_life_days, kg_per_meal)
        OVERRIDING SYSTEM VALUE VALUES (%s,%s,%s,%s,%s)
    """, [(i, n, r, s, k) for i, (n, r, s, k) in enumerate(CATEGORIES, start=1)])

    cat_id = {name: i for i, (name, *_rest) in enumerate(CATEGORIES, start=1)}
    needs_refrig = {i: r for i, (_n, r, _s, _k) in enumerate(CATEGORIES, start=1)}
    cat_meals = {i: k for i, (_n, _r, _s, k) in enumerate(CATEGORIES, start=1)}

    # -- recipient capabilities (M:N #1) ----------------------------------
    caps = []
    for oid in recipient_ids:
        chosen = RNG.sample(list(cat_id.values()), RNG.randint(6, 10))
        for c in chosen:
            caps.append((oid, c, round(RNG.uniform(150, 1200), 0)))
    cur.executemany(
        "INSERT INTO org_capabilities (org_id, category_id, max_weekly_kg) VALUES (%s,%s,%s)",
        caps,
    )
    capable = {}     # category -> [recipient ids]
    for oid, c, _m in caps:
        capable.setdefault(c, []).append(oid)

    # -- donation batches -------------------------------------------------
    donor_kind = {i: kind for i, (_n, kind) in zip(donor_ids, DONOR_NAMES)}
    donor_cats = {
        oid: [cat_id[c] for c in DONOR_KINDS[donor_kind[oid]]]
        for oid in donor_ids
    }
    donor_staff = {u[4]: u[0] for u in users if u[3] == DONOR}

    batch_total, fresh_count, claimed_count = 12000, 60, 40
    dormant_donors = set(donor_ids[-4:])       # partners who stopped donating
    batches = []          # dicts
    for bid in range(1, batch_total + 1):
        if bid <= fresh_count:
            donor = RNG.choice([d for d in donor_ids if d not in dormant_donors])
        else:
            donor = RNG.choice(donor_ids)
        cat = RNG.choice(donor_cats[donor])
        qty = float(RNG.choice([15, 20, 25, 30, 35, 40, 45, 50, 60, 75, 90, 120, 150]))
        shelf = CATEGORIES[cat - 1][2]
        storage = round(RNG.uniform(-2.0, 4.0), 1) if needs_refrig[cat] else round(RNG.uniform(12.0, 21.0), 1)

        if bid <= fresh_count:
            prepared = NOW - timedelta(hours=RNG.randint(2, 20))
            status = "available"
        elif bid <= fresh_count + claimed_count:
            prepared = NOW - timedelta(hours=RNG.randint(20, 40))
            status = "claimed"
        else:
            # dormant partners only appear in the deep history
            low = 45 if donor in dormant_donors else 3
            prepared = NOW - timedelta(days=RNG.randint(low, 120), hours=RNG.randint(0, 12))
            status = RNG.choices(
                ["delivered", "spoiled", "withdrawn"], weights=[0.82, 0.10, 0.08]
            )[0]
        expires = prepared + timedelta(days=shelf, hours=RNG.randint(0, 8))

        # a dozen "available" batches are already close to expiry, so the
        # at-risk pipeline has something meaningful to work with
        if 13 <= bid <= 24:
            prepared = NOW - timedelta(hours=RNG.randint(30, 60))
            expires = NOW + timedelta(hours=RNG.randint(4, 44))

        batches.append({
            "id": bid, "donor": donor, "cat": cat, "qty": qty,
            "prepared": prepared, "expires": expires, "temp": storage,
            "status": status, "desc": RNG.choice(ITEMS[CATEGORIES[cat - 1][0]]),
            "by": donor_staff[donor],
        })

    cur.executemany("""
        INSERT INTO donation_batches (batch_id, donor_org_id, category_id,
              item_description, quantity_kg, prepared_at, expires_at,
              storage_temp_c, status, created_by, created_at)
        OVERRIDING SYSTEM VALUE
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, [
        (b["id"], b["donor"], b["cat"], b["desc"], b["qty"], b["prepared"],
         b["expires"], b["temp"], b["status"], b["by"], b["prepared"])
        for b in batches
    ])

    # -- allocations + handovers (M:N #2) ---------------------------------
    allocs = []        # (id, batch, recipient, kg, status, at, note)
    for b in batches:
        if b["status"] in ("spoiled", "withdrawn"):
            continue
        pool = capable.get(b["cat"], [])
        if not pool:
            continue
        if b["status"] == "delivered":
            n = RNG.choices([1, 2, 3], weights=[0.55, 0.32, 0.13])[0]
            shares = split_kg(b["qty"], n)
            status, at = "handed_over", b["prepared"] + timedelta(hours=RNG.randint(2, 30))
        elif b["status"] == "claimed":
            shares = [round(b["qty"] * RNG.uniform(0.55, 0.95), 1)]
            status, at = "reserved", b["prepared"] + timedelta(hours=RNG.randint(1, 6))
        else:   # available: sometimes partially claimed already
            if RNG.random() > 0.22:
                continue
            shares = [round(b["qty"] * RNG.uniform(0.25, 0.45), 1)]
            status, at = "reserved", NOW - timedelta(hours=RNG.randint(1, 5))
        picks = RNG.sample(pool, min(len(shares), len(pool)))
        if len(picks) < len(shares):          # merge leftover kg into last share
            shares[len(picks) - 1] = round(sum(shares[len(picks) - 1:]), 1)
            shares = shares[:len(picks)]
        for rid, kg in zip(picks, shares):
            allocs.append((len(allocs) + 1, b["id"], rid, kg, status, at,
                           None if RNG.random() > 0.15 else "Check quantity on arrival"))

    cur.executemany("""
        INSERT INTO batch_allocations (allocation_id, batch_id, recipient_org_id,
              allocated_kg, status, allocated_at, note)
        OVERRIDING SYSTEM VALUE VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, allocs)

    handovers = []
    for aid, _bid, _rid, kg, status, at, _note in allocs:
        if status != "handed_over":
            continue
        received = round(kg * RNG.uniform(0.9, 1.0), 2)
        cond = "good" if received >= kg * 0.98 else "partial"
        if RNG.random() < 0.04:
            cond = "rejected"
            received = 0.0
        handovers.append((aid, person(), received, cond,
                          at + timedelta(hours=RNG.randint(1, 6)),
                          None if cond == "good" else "Arrived late / short weight"))
    cur.executemany("""
        INSERT INTO handovers (allocation_id, received_by_name, received_kg,
              condition, signed_at, notes)
        VALUES (%s,%s,%s,%s,%s,%s)
    """, handovers)

    # -- vehicles ---------------------------------------------------------
    vehicles = []
    for vid in range(1, 25):
        refrigerated = vid % 3 != 0
        vehicles.append((vid, f"34 RL {vid:03d}", RNG.choice([350.0, 500.0, 750.0, 1000.0]),
                         refrigerated, RNG.choice(recipient_ids), True))
    cur.executemany("""
        INSERT INTO vehicles (vehicle_id, plate, capacity_kg, is_refrigerated,
              owner_org_id, is_active)
        OVERRIDING SYSTEM VALUE VALUES (%s,%s,%s,%s,%s,%s)
    """, vehicles)

    # -- skills + volunteer certifications (M:N #4) -----------------------
    cur.executemany("""
        INSERT INTO skills (skill_id, name, description) OVERRIDING SYSTEM VALUE
        VALUES (%s,%s,%s)
    """, [(i, n, d) for i, (n, d) in enumerate(SKILLS, start=1)])

    VOL_SKILLS = []      # (user, skill, certified_on, expires_on)
    for uid in courier_ids:
        born = NOW.date() - timedelta(days=RNG.randint(400, 1500))
        VOL_SKILLS.append((uid, 1, born, born + timedelta(days=365)))
        for skill, prob in ((2, 0.62), (3, 0.55), (4, 0.35), (5, 0.25), (6, 0.10)):
            if RNG.random() < prob:
                start = born + timedelta(days=RNG.randint(0, 300))
                VOL_SKILLS.append((uid, skill, start, start + timedelta(days=365)))
    cur.executemany("""
        INSERT INTO volunteer_skills (user_id, skill_id, certified_on, expires_on)
        VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING
    """, VOL_SKILLS)

    licenced = [uid for uid in courier_ids
                if uid in {(s[0]) for s in VOL_SKILLS if s[1] == 2}]

    # -- delivery runs, run_batches (M:N #3), shifts (M:N #5) -------------
    by_id = {b["id"]: b for b in batches}
    delivered = [b for b in batches if b["status"] == "delivered"]
    RNG.shuffle(delivered)
    cursor = 0
    runs, run_batch_rows, shifts = [], [], []
    for run_id in range(1, 1201):
        k = RNG.randint(1, 5)
        take = delivered[cursor:cursor + k]
        cursor += k
        if not take:
            break
        run_date = (max(b["prepared"] for b in take) + timedelta(days=1)).date()
        if run_date > NOW.date():
            run_date = NOW.date()
        total_kg = sum(b["qty"] for b in take)
        need_cold = any(needs_refrig[b["cat"]] for b in take)

        candidates = [v for v in vehicles
                      if v[3] or not need_cold]
        candidates = [v for v in candidates if v[2] >= total_kg] or candidates or vehicles
        vehicle = RNG.choice(candidates)

        driver = RNG.choice(licenced)
        crew = [driver] + RNG.sample([c for c in courier_ids if c != driver],
                                     RNG.randint(0, 2))
        runs.append((run_id, run_date, vehicle[0], RNG.choice(coord_ids),
                     "completed" if run_date < NOW.date() - timedelta(days=1) else "planned",
                     datetime.combine(run_date, datetime.min.time(), tzinfo=timezone.utc)
                     + timedelta(hours=RNG.randint(8, 11)),
                     datetime.combine(run_date, datetime.min.time(), tzinfo=timezone.utc)
                     + timedelta(hours=RNG.randint(12, 18)),
                     round(RNG.uniform(8, 48), 1) if run_date < NOW.date() else None))
        for seq, b in enumerate(take, start=1):
            run_batch_rows.append((run_id, b["id"], seq))
        for pos, uid in enumerate(crew):
            shifts.append((run_id, uid, "driver" if pos == 0 else "loader",
                           round(RNG.uniform(1.5, 6.0), 1)))

    cur.executemany("""
        INSERT INTO delivery_runs (run_id, run_date, vehicle_id, lead_user_id,
              status, started_at, completed_at, total_km)
        OVERRIDING SYSTEM VALUE VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
    """, runs)
    cur.executemany(
        "INSERT INTO run_batches (run_id, batch_id, sequence_no) VALUES (%s,%s,%s)",
        run_batch_rows)
    cur.executemany("""
        INSERT INTO shift_assignments (run_id, user_id, role_on_run, hours_logged)
        VALUES (%s,%s,%s,%s)
    """, shifts)

    # -- cold chain checks ------------------------------------------------
    checks = []
    for b in batches:
        n = 2 if b["status"] in ("delivered", "claimed") else 1
        for i in range(n):
            if needs_refrig[b["cat"]]:
                temp = round(RNG.uniform(-2.0, 8.0), 1)
                if RNG.random() < (0.55 if b["status"] == "spoiled" else 0.05):
                    temp = round(RNG.uniform(9.0, 15.0), 1)
            else:
                temp = round(RNG.uniform(10.0, 23.0), 1)
            passed = temp <= 8.0 if needs_refrig[b["cat"]] else temp <= 25.0
            checks.append((b["id"], RNG.choice(coord_ids + courier_ids), temp, passed,
                           b["prepared"] + timedelta(hours=1 + i * 5),
                           None if passed else "Above cold-chain limit"))
    cur.executemany("""
        INSERT INTO cold_chain_checks (batch_id, checked_by, temp_c, passed,
              checked_at, notes)
        VALUES (%s,%s,%s,%s,%s,%s)
    """, checks)

    # -- audit trail ------------------------------------------------------
    actions = [
        ("batch.create", "donation_batches"), ("batch.withdraw", "donation_batches"),
        ("allocation.create", "batch_allocations"), ("handover.sign", "handovers"),
        ("run.assign", "delivery_runs"), ("login", "users"),
        ("org.update", "organizations"), ("coldcheck.record", "cold_chain_checks"),
    ]
    audit = []
    for _ in range(1500):
        action, entity = RNG.choice(actions)
        actor = RNG.choice([u[0] for u in users])
        audit.append((actor, action, entity, str(RNG.randint(1, 500)),
                      Json({"ip": f"10.0.{RNG.randint(0,9)}.{RNG.randint(2,240)}",
                            "seed": SEED}),
                      NOW - timedelta(days=RNG.randint(0, 120), minutes=RNG.randint(0, 1439))))
    cur.executemany("""
        INSERT INTO audit_log (actor_user_id, action, entity, entity_id, details, created_at)
        VALUES (%s,%s,%s,%s,%s,%s)
    """, audit)

    # -- keep identity sequences ahead of the explicit ids ----------------
    for table, column in [
        ("roles", "role_id"), ("organizations", "org_id"), ("users", "user_id"),
        ("food_categories", "category_id"), ("donation_batches", "batch_id"),
        ("batch_allocations", "allocation_id"), ("vehicles", "vehicle_id"),
        ("delivery_runs", "run_id"), ("skills", "skill_id"),
        ("cold_chain_checks", "check_id"), ("handovers", "handover_id"),
        ("audit_log", "audit_id"),
    ]:
        cur.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', '{column}'), "
            f"COALESCE((SELECT max({column}) FROM {table}), 1))"
        )

    cur.execute("ANALYZE")
    conn.commit()

    tables = [r[0] for r in cur.execute("""
        SELECT relname FROM pg_stat_user_tables ORDER BY relname
    """).fetchall()]
    print(f"Seeded database (seed={SEED})")
    total = 0
    for table in tables:
        n = cur.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        total += n
        print(f"  {table:<22} {n:>6}")
    print(f"  {'TOTAL':<22} {total:>6} rows")
    print(f"\nDemo login: admin@rescuelink.org / donor@rescuelink.org / "
          f"coordinator@rescuelink.org / courier@rescuelink.org  --  password {DEMO_PASSWORD}")
    conn.close()


if __name__ == "__main__":
    main()
