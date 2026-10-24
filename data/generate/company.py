"""Harborline Provisions: entities, chart of accounts, vendors, items. All synthetic.

Vendor names are built from generic word lists; any match with a real business is coincidental.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from probation.sync.job import norm_name

ENTITIES = [
    # name, company_name, operating bank account at SimBank
    ("HPI", "Harborline Provisions Inc.", "SIM-OPS-HPI-01"),
    ("HCC", "Harborline Cold Chain LLC", "SIM-OPS-HCC-01"),
    ("HFM", "Harborline Fresh Markets LLC", "SIM-OPS-HFM-01"),
]

# Share of vendor count per group, and the entity that mostly buys from it.
# Spend weights are calibrated loosely to a broadline food distributor's cost mix (see docs/DATA.md).
GROUPS = {
    "Produce":               dict(n=70, spend=1.0,  entity="HPI", po=True,  per_month=(4, 14)),
    "Dairy":                 dict(n=30, spend=1.1,  entity="HPI", po=True,  per_month=(4, 12)),
    "Meat & Seafood":        dict(n=50, spend=1.8,  entity="HPI", po=True,  per_month=(3, 10)),
    "Dry Goods":             dict(n=60, spend=0.9,  entity="HPI", po=True,  per_month=(2, 8)),
    "Beverage":              dict(n=30, spend=0.8,  entity="HFM", po=True,  per_month=(2, 6)),
    "Packaging":             dict(n=30, spend=0.5,  entity="HPI", po=True,  per_month=(1, 4)),
    "Fuel & Fleet":          dict(n=20, spend=0.7,  entity="HCC", po=False, per_month=(2, 6)),
    "Facilities":            dict(n=30, spend=0.4,  entity="HFM", po=True,  per_month=(0, 2)),
    "Logistics":             dict(n=20, spend=0.9,  entity="HCC", po=True,  per_month=(1, 4)),
    "Professional Services": dict(n=20, spend=1.2,  entity="HPI", po=False, per_month=(0, 1)),
    "Utilities":             dict(n=12, spend=0.6,  entity="HCC", po=False, per_month=(1, 1)),
    "IT & Software":         dict(n=26, spend=0.5,  entity="HPI", po=False, per_month=(0, 1)),
}

DEPARTMENTS = {
    "Produce": ("Purchasing - Fresh", "Dana Whitfield"),
    "Dairy": ("Purchasing - Fresh", "Dana Whitfield"),
    "Meat & Seafood": ("Purchasing - Protein", "Luis Ortega"),
    "Dry Goods": ("Purchasing - Center Store", "Priya Raman"),
    "Beverage": ("Store Operations", "Kenji Mori"),
    "Packaging": ("Warehouse", "Tomasz Novak"),
    "Fuel & Fleet": ("Fleet", "Rhonda Bell"),
    "Facilities": ("Facilities", "Grace Achebe"),
    "Logistics": ("Fleet", "Rhonda Bell"),
    "Professional Services": ("Finance", "Marisol Vega"),
    "Utilities": ("Facilities", "Grace Achebe"),
    "IT & Software": ("IT", "Sam Okafor"),
}

ITEMS = {
    "Produce": [("Hass avocados 48ct", "case", 6000), ("Romaine hearts 24ct", "case", 3450), ("Yellow onions 50lb", "sack", 2875),
                ("Roma tomatoes 25lb", "case", 3120), ("Lemons 115ct", "case", 4290), ("Russet potatoes 50lb", "sack", 2460),
                ("Strawberries 8x1lb", "flat", 2980), ("Baby spinach 4x2.5lb", "case", 3675), ("Bell peppers mixed 25lb", "case", 4150)],
    "Dairy": [("Whole milk 4x1gal", "case", 2140), ("Unsalted butter 36x1lb", "case", 9850), ("Heavy cream 12qt", "case", 5260),
              ("Cheddar block 2x5lb", "case", 4720), ("Greek yogurt 6x32oz", "case", 3390), ("Large eggs 15dz", "case", 4875)],
    "Meat & Seafood": [("Chicken thighs boneless 40lb", "case", 9640), ("Ground beef 80/20 4x10lb", "case", 16480),
                       ("Atlantic salmon fillet 10lb", "case", 13950), ("Pork shoulder 2x9lb", "case", 6230),
                       ("Shrimp 16/20 5x2lb", "case", 11875), ("Turkey breast 2x8lb", "case", 8120)],
    "Dry Goods": [("All-purpose flour 50lb", "bag", 2390), ("Jasmine rice 50lb", "bag", 4180), ("Canola oil 35lb", "jug", 4560),
                  ("Canned tomatoes 6x#10", "case", 3270), ("Black beans 6x#10", "case", 2890), ("Granulated sugar 50lb", "bag", 3640),
                  ("Spaghetti 2x10lb", "case", 2150)],
    "Beverage": [("Sparkling water 24x12oz", "case", 1490), ("Cold brew concentrate 4x64oz", "case", 5420),
                 ("Orange juice 8x52oz", "case", 3360), ("Kombucha assorted 12ct", "case", 3180)],
    "Packaging": [("Kraft clamshell 200ct", "case", 6840), ("Stretch film 18in 4 rolls", "case", 7420), ("Corrugated box 18x12x10 25ct", "bundle", 3150),
                  ("Deli containers 32oz 240ct", "case", 5980)],
    "Fuel & Fleet": [("Diesel fuel", "gal", 489), ("Reefer unit service", "each", 42500), ("Tire replacement 22.5in", "each", 51800),
                     ("DEF fluid 2.5gal", "jug", 1890)],
    "Facilities": [("Walk-in cooler repair", "job", 185000), ("HVAC quarterly service", "job", 92000), ("Pest control monthly", "visit", 26500),
                   ("Dock door repair", "job", 74000), ("Floor scrubbing service", "visit", 41000)],
    "Logistics": [("LTL freight - regional", "load", 128000), ("Pallet rental 50ct", "lot", 42500), ("Cold storage overflow", "pallet-week", 3800)],
    "Professional Services": [("Monthly bookkeeping review", "month", 450000), ("Legal services", "hour", 39500),
                              ("Food safety audit", "audit", 680000), ("Payroll processing", "month", 210000)],
    "Utilities": [("Electricity", "month", 1840000), ("Water and sewer", "month", 312000), ("Natural gas", "month", 486000),
                  ("Waste hauling", "month", 228000)],
    "IT & Software": [("Warehouse management licence", "month", 480000), ("Route planning SaaS", "month", 215000),
                      ("Handheld scanner lease", "month", 94000), ("Network support", "month", 160000)],
}

PREFIX = ["Pacific", "Sierra", "Golden", "Bay", "Redwood", "Coastal", "Valley", "Summit", "Harvest", "Delta", "Cedar",
          "Granite", "Sunrise", "Northgate", "Willow", "Mesa", "Juniper", "Copper", "Tidewater", "Foothill", "Orchard",
          "Lakeside", "Sutro", "Kestrel", "Marin", "Alder", "Bluewater", "Canyon", "Elkhorn", "Fairview", "Highline",
          "Ironwood", "Larkspur", "Meadowbrook", "Oakridge", "Pinecrest", "Quarry", "Riverbend", "Silverleaf", "Tamarack",
          "Westfield", "Yarrow", "Bramble", "Crestline", "Driftwood", "Eastbay", "Fernhill", "Gullwing", "Hollister", "Inland"]
NOUN = {
    "Produce": ["Produce", "Farms", "Growers", "Fresh", "Orchards", "Harvest Co."],
    "Dairy": ["Dairy", "Creamery", "Dairy Coop", "Milk Co."],
    "Meat & Seafood": ["Seafood", "Meats", "Fisheries", "Provisions", "Poultry"],
    "Dry Goods": ["Foods", "Pantry Supply", "Milling", "Trading Co.", "Wholesale"],
    "Beverage": ["Beverage", "Bottling", "Drinks Co.", "Brewing"],
    "Packaging": ["Packaging", "Paper Goods", "Container Supply", "Pack Co."],
    "Fuel & Fleet": ["Fuel", "Fleet Services", "Truck & Trailer", "Tire Co."],
    "Facilities": ["Refrigeration", "Mechanical", "Facility Services", "Pest Control", "Equipment Repair"],
    "Logistics": ["Freight", "Logistics", "Transport", "Cold Storage"],
    "Professional Services": ["Advisors", "Legal LLP", "Consulting", "Audit Partners"],
    "Utilities": ["Power", "Water District", "Gas & Electric", "Waste Services"],
    "IT & Software": ["Software", "Systems", "Networks", "Tech"],
}
SUFFIX = ["", "", "", " Inc.", " LLC", " Co.", " & Sons", " Group"]
TLDS = [".com", ".com", ".com", ".net", ".co", ".us", ".coop"]
BANKS = ["Bayview Commercial Bank", "Cascade Federal", "Tidewater Savings", "Ridgemont Credit Union", "Golden Spur Bank",
         "Northshore Trust", "Valley Merchants Bank", "Summit National", "Cypress Community Bank", "Ironbridge Bank"]


@dataclass
class VendorSpec:
    sid: str
    name: str
    group: str
    entity: str
    domain: str
    tax_id: str
    phone: str
    created_at: str
    weight: float
    bank: list[dict] = field(default_factory=list)   # history, oldest first
    represents_company: str | None = None


def slug(name: str) -> str:
    return "".join(ch for ch in name.lower().replace("&", "and") if ch.isalnum())


def make_vendors(rng: random.Random) -> list[VendorSpec]:
    used: set[str] = set()
    out: list[VendorSpec] = []
    n = 0
    for group, g in GROUPS.items():
        for _ in range(g["n"]):
            for _try in range(50):
                name = f"{rng.choice(PREFIX)} {rng.choice(NOUN[group])}{rng.choice(SUFFIX)}".replace("Co. Co.", "Co.")
                key = norm_name(name)
                if key not in used:
                    break
            used.add(key)
            n += 1
            base = slug(name.replace(" Inc.", "").replace(" LLC", "").replace(" Group", ""))
            created = f"2024-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
            v = VendorSpec(
                sid=f"SUP-{n:04d}", name=name, group=group, entity=g["entity"] if rng.random() < 0.85 else rng.choice(["HPI", "HCC", "HFM"]),
                domain=base + rng.choice(TLDS), tax_id=f"00-{rng.randint(1000000, 9999999)}",
                phone=f"(555) {rng.randint(200, 899)}-{rng.randint(1000, 9999)}", created_at=created,
                weight=rng.paretovariate(1.6),
            )
            v.bank.append(dict(bank_name=rng.choice(BANKS), routing_last4=f"{rng.randint(0, 9999):04d}",
                               account_last4=f"{rng.randint(0, 9999):04d}", valid_from=created, source="onboarding",
                               verified_by="Marisol Vega", verified_at=created))
            out.append(v)
    # intercompany: the cold-chain entity bills the distributor for storage and haulage
    for ent, code in [("HCC", "Harborline Cold Chain LLC"), ("HFM", "Harborline Fresh Markets LLC")]:
        n += 1
        out.append(VendorSpec(sid=f"SUP-{n:04d}", name=code, group="Logistics" if ent == "HCC" else "Dry Goods", entity="HPI",
                              domain="harborline-provisions.com", tax_id=f"00-{rng.randint(1000000, 9999999)}",
                              phone="(555) 300-1000", created_at="2023-01-01", weight=1.5,
                              bank=[dict(bank_name="Bayview Commercial Bank", routing_last4="0001", account_last4=f"{rng.randint(0, 9999):04d}",
                                         valid_from="2023-01-01", source="onboarding", verified_by="Marisol Vega", verified_at="2023-01-01")],
                              represents_company=ent))
    return out
