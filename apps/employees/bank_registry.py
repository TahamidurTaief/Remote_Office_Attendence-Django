"""
apps/employees/bank_registry.py
Centralized Bangladesh Bank directory registry and choices.
Official schedule verified against FID (Financial Institutions Division):
https://fid.gov.bd/pages/static-pages/694032ba35ce18e1c05614ba
and Bangladesh Bank Schedule of Banks.
"""

from typing import Dict, List, Tuple

# Grouped definitions for UI select optgroups & validation
BANGLADESH_BANK_GROUPS: List[Tuple[str, List[Tuple[str, str]]]] = [
    (
        "Central Bank",
        [
            ("Bangladesh Bank", "Bangladesh Bank"),
        ],
    ),
    (
        "State-Owned Banks",
        [
            ("Sonali Bank Limited", "Sonali Bank Limited"),
            ("Janata Bank Limited", "Janata Bank Limited"),
            ("Agrani Bank Limited", "Agrani Bank Limited"),
            ("Rupali Bank Limited", "Rupali Bank Limited"),
            ("Bangladesh Krishi Bank", "Bangladesh Krishi Bank"),
            ("Rajshahi Krishi Unnayan Bank", "Rajshahi Krishi Unnayan Bank"),
            ("Bangladesh Development Bank Limited", "Bangladesh Development Bank Limited"),
            ("BASIC Bank Limited", "BASIC Bank Limited"),
            ("Ansar-VDP Unnayan Bank", "Ansar-VDP Unnayan Bank"),
            ("Karmasangsthan Bank", "Karmasangsthan Bank"),
        ],
    ),
    (
        "Private Banks",
        [
            ("AB Bank Limited", "AB Bank Limited"),
            ("Al-Arafah Islami Bank Limited", "Al-Arafah Islami Bank Limited"),
            ("Bangladesh Commerce Bank Limited", "Bangladesh Commerce Bank Limited"),
            ("Bank Asia Limited", "Bank Asia Limited"),
            ("BRAC Bank Limited", "BRAC Bank Limited"),
            ("Dhaka Bank Limited", "Dhaka Bank Limited"),
            ("Dutch-Bangla Bank Limited", "Dutch-Bangla Bank Limited"),
            ("Eastern Bank Limited", "Eastern Bank Limited"),
            ("EXIM Bank Limited", "EXIM Bank Limited"),
            ("First Security Islami Bank Limited", "First Security Islami Bank Limited"),
            ("ICB Islamic Bank Limited", "ICB Islamic Bank Limited"),
            ("IFIC Bank Limited", "IFIC Bank Limited"),
            ("Islami Bank Bangladesh Limited", "Islami Bank Bangladesh Limited"),
            ("Jamuna Bank Limited", "Jamuna Bank Limited"),
            ("Meghna Bank Limited", "Meghna Bank Limited"),
            ("Mercantile Bank Limited", "Mercantile Bank Limited"),
            ("Midland Bank Limited", "Midland Bank Limited"),
            ("Mutual Trust Bank Limited", "Mutual Trust Bank Limited"),
            ("National Bank Limited", "National Bank Limited"),
            ("NRB Bank Limited", "NRB Bank Limited"),
            ("NCC Bank Limited", "NCC Bank Limited"),
            ("NRB Commercial Bank Limited", "NRB Commercial Bank Limited"),
            ("ONE Bank Limited", "ONE Bank Limited"),
            ("Premier Bank Limited", "Premier Bank Limited"),
            ("Prime Bank Limited", "Prime Bank Limited"),
            ("Pubali Bank Limited", "Pubali Bank Limited"),
            ("Shahjalal Islami Bank Limited", "Shahjalal Islami Bank Limited"),
            ("Social Islami Bank Limited", "Social Islami Bank Limited"),
            ("South Bangla Agriculture and Commerce Bank Limited", "South Bangla Agriculture and Commerce Bank Limited"),
            ("Southeast Bank Limited", "Southeast Bank Limited"),
            ("Standard Bank Limited", "Standard Bank Limited"),
            ("The City Bank Limited", "The City Bank Limited"),
            ("The Farmers Bank Limited", "The Farmers Bank Limited"),
            ("Trust Bank Limited", "Trust Bank Limited"),
            ("Union Bank Limited", "Union Bank Limited"),
            ("United Commercial Bank Limited", "United Commercial Bank Limited"),
            ("Uttara Bank Limited", "Uttara Bank Limited"),
        ],
    ),
    (
        "Foreign Banks",
        [
            ("Bank Alfalah Limited", "Bank Alfalah Limited"),
            ("Citibank N.A.", "Citibank N.A."),
            ("Commercial Bank of Ceylon Limited", "Commercial Bank of Ceylon Limited"),
            ("Habib Bank Limited", "Habib Bank Limited"),
            ("National Bank of Pakistan", "National Bank of Pakistan"),
            ("Standard Chartered Bank", "Standard Chartered Bank"),
            ("State Bank of India", "State Bank of India"),
            ("HSBC Limited", "HSBC Limited"),
            ("Woori Bank", "Woori Bank"),
        ],
    ),
]

# Flat choices list with empty choice first, followed by optgroups
BANGLADESH_BANK_CHOICES = [("", "-- Choose Bank --")] + BANGLADESH_BANK_GROUPS

# Set of all valid canonical bank names
ALL_CANONICAL_BANK_NAMES = set()
for _, banks in BANGLADESH_BANK_GROUPS:
    for val, _ in banks:
        ALL_CANONICAL_BANK_NAMES.add(val)

# Backward-compatibility alias dictionary for matching historical / PLC names
BANK_ALIASES: Dict[str, str] = {
    # PLC <-> Limited variations
    "dutch-bangla bank plc": "Dutch-Bangla Bank Limited",
    "brac bank plc": "BRAC Bank Limited",
    "the city bank plc": "The City Bank Limited",
    "city bank": "The City Bank Limited",
    "city bank ltd": "The City Bank Limited",
    "city bank limited": "The City Bank Limited",
    "eastern bank plc": "Eastern Bank Limited",
    "sonali bank plc": "Sonali Bank Limited",
    "janata bank plc": "Janata Bank Limited",
    "agrani bank plc": "Agrani Bank Limited",
    "rupali bank plc": "Rupali Bank Limited",
    "islami bank bangladesh plc": "Islami Bank Bangladesh Limited",
    "united commercial bank plc": "United Commercial Bank Limited",
    "ucb": "United Commercial Bank Limited",
    "mutual trust bank plc": "Mutual Trust Bank Limited",
    "prime bank plc": "Prime Bank Limited",
    "dhaka bank plc": "Dhaka Bank Limited",
    "bank asia plc": "Bank Asia Limited",
    "pubali bank plc": "Pubali Bank Limited",
    "uttara bank plc": "Uttara Bank Limited",
    "trust bank plc": "Trust Bank Limited",
    "southeast bank plc": "Southeast Bank Limited",
    "padma bank limited": "The Farmers Bank Limited",
    "padma bank plc": "The Farmers Bank Limited",
    "farmers bank": "The Farmers Bank Limited",
    "the farmers bank": "The Farmers Bank Limited",
    "the farmers bank limited": "The Farmers Bank Limited",
    "hsbc": "HSBC Limited",
    "hsbc bangladesh": "HSBC Limited",
    "the hong kong and sanghai banking corporation ltd.": "HSBC Limited",
    "the hong kong and shanghai banking corporation ltd.": "HSBC Limited",
    "bank al-falah limited": "Bank Alfalah Limited",
    "ansar vdp unnayan bank": "Ansar-VDP Unnayan Bank",
    "karmasangthan bank": "Karmasangsthan Bank",
    "national credit & commerc bank limited": "NCC Bank Limited",
    "national credit and commerce bank limited": "NCC Bank Limited",
    "south bangla agriculture & commerce bank limited": "South Bangla Agriculture and Commerce Bank Limited",
    "one bank limited": "ONE Bank Limited",
    "basic bank limited": "BASIC Bank Limited",
}


def resolve_canonical_bank_name(raw_name: str) -> str:
    """
    Resolves any bank string (with variations, PLC/Ltd suffixes, or aliases)
    to its canonical name from BANGLADESH_BANK_CHOICES.
    If already canonical, returns it. If alias match found, returns canonical.
    Otherwise returns raw_name stripped.
    """
    if not raw_name:
        return ""
    cleaned = raw_name.strip()
    if cleaned in ALL_CANONICAL_BANK_NAMES:
        return cleaned

    lower = cleaned.lower()
    if lower in BANK_ALIASES:
        return BANK_ALIASES[lower]

    # Try removing trailing "plc", "limited", "ltd", "bank" etc.
    clean_base = lower.replace("plc", "").replace("limited", "").replace("ltd.", "").replace("ltd", "").strip()
    for name in ALL_CANONICAL_BANK_NAMES:
        target_base = name.lower().replace("limited", "").replace("ltd.", "").replace("ltd", "").strip()
        if clean_base == target_base or clean_base == target_base.replace("bank", "").strip():
            return name

    return cleaned
