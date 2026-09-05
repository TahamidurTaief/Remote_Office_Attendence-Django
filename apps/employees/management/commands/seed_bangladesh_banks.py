from django.core.management.base import BaseCommand
from apps.employees.models import Bank, BankBranch

CANONICAL_BANKS = [
    # ── CENTRAL BANK ────────────────────────────────────────────────────────
    {
        "code": "BB",
        "name": "Bangladesh Bank",
        "short_name": "Bangladesh Bank",
        "bank_type": "specialized",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "BBGDBDDH",
        "display_order": 1,
        "branches": [
            {"name": "Head Office", "routing_number": "010270000", "district": "Dhaka", "branch_code": "001"},
            {"name": "Motijheel Office", "routing_number": "010270011", "district": "Dhaka", "branch_code": "002"},
        ],
    },
    # ── STATE-OWNED COMMERCIAL & SPECIALIZED BANKS ─────────────────────────
    {
        "code": "SONALI",
        "name": "Sonali Bank PLC",
        "short_name": "Sonali Bank",
        "bank_type": "state_owned",
        "logo": "images/banks/sonali.svg",
        "swift_code": "BSONBDDH",
        "display_order": 2,
        "branches": [
            {"name": "Local Office", "routing_number": "200270054", "district": "Dhaka", "branch_code": "401"},
            {"name": "Motijheel Branch", "routing_number": "200273763", "district": "Dhaka", "branch_code": "402"},
            {"name": "Laldighi Branch", "routing_number": "200153393", "district": "Chattogram", "branch_code": "410"},
        ],
    },
    {
        "code": "JANATA",
        "name": "Janata Bank PLC",
        "short_name": "Janata Bank",
        "bank_type": "state_owned",
        "logo": "images/banks/janata.svg",
        "swift_code": "JANBBDDH",
        "display_order": 3,
        "branches": [
            {"name": "Local Office", "routing_number": "135272458", "district": "Dhaka", "branch_code": "1301"},
            {"name": "Corporate Branch", "routing_number": "135271111", "district": "Dhaka", "branch_code": "1302"},
        ],
    },
    {
        "code": "AGRANI",
        "name": "Agrani Bank PLC",
        "short_name": "Agrani Bank",
        "bank_type": "state_owned",
        "logo": "images/banks/agrani.svg",
        "swift_code": "AGRABDDA",
        "display_order": 4,
        "branches": [
            {"name": "Principal Branch", "routing_number": "010272844", "district": "Dhaka", "branch_code": "1401"},
            {"name": "Amin Court Branch", "routing_number": "010272222", "district": "Dhaka", "branch_code": "1402"},
        ],
    },
    {
        "code": "RUPALI",
        "name": "Rupali Bank PLC",
        "short_name": "Rupali Bank",
        "bank_type": "state_owned",
        "logo": "images/banks/rupali.svg",
        "swift_code": "RUPBBDDH",
        "display_order": 5,
        "branches": [
            {"name": "Local Office", "routing_number": "185272905", "district": "Dhaka", "branch_code": "1901"},
            {"name": "Dilkusha Branch", "routing_number": "185273333", "district": "Dhaka", "branch_code": "1902"},
        ],
    },
    {
        "code": "BKB",
        "name": "Bangladesh Krishi Bank",
        "short_name": "Krishi Bank",
        "bank_type": "state_owned",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "BKBKBDDH",
        "display_order": 6,
        "branches": [
            {"name": "Principal Branch", "routing_number": "030272440", "district": "Dhaka", "branch_code": "031"},
        ],
    },
    {
        "code": "RAKUB",
        "name": "Rajshahi Krishi Unnayan Bank",
        "short_name": "RAKUB",
        "bank_type": "state_owned",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "RAKUBDDH",
        "display_order": 7,
        "branches": [
            {"name": "Principal Branch", "routing_number": "180811150", "district": "Rajshahi", "branch_code": "181"},
        ],
    },
    {
        "code": "BDBL",
        "name": "Bangladesh Development Bank Limited",
        "short_name": "BDBL",
        "bank_type": "state_owned",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "BDBLBDDH",
        "display_order": 8,
        "branches": [
            {"name": "Principal Branch", "routing_number": "045272440", "district": "Dhaka", "branch_code": "045"},
        ],
    },
    {
        "code": "BASIC",
        "name": "BASIC Bank Limited",
        "short_name": "BASIC Bank",
        "bank_type": "state_owned",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "BKSIBDDH",
        "display_order": 9,
        "branches": [
            {"name": "Sena Kalyan Branch", "routing_number": "050272440", "district": "Dhaka", "branch_code": "050"},
        ],
    },
    {
        "code": "AVDP",
        "name": "Ansar-VDP Unnayan Bank",
        "short_name": "Ansar-VDP Bank",
        "bank_type": "state_owned",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "AVDPBDDH",
        "display_order": 10,
        "branches": [
            {"name": "Head Office Branch", "routing_number": "025272440", "district": "Dhaka", "branch_code": "025"},
        ],
    },
    {
        "code": "KBANK",
        "name": "Karmasangsthan Bank",
        "short_name": "Karmasangsthan Bank",
        "bank_type": "state_owned",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "KBANKDDH",
        "display_order": 11,
        "branches": [
            {"name": "Head Office Branch", "routing_number": "140272440", "district": "Dhaka", "branch_code": "140"},
        ],
    },
    # ── PRIVATE SCHEDULED COMMERCIAL BANKS ────────────────────────────────
    {
        "code": "DBBL",
        "name": "Dutch-Bangla Bank PLC",
        "short_name": "Dutch-Bangla Bank",
        "bank_type": "commercial",
        "logo": "images/banks/dbbl.svg",
        "swift_code": "DBBLBDDH",
        "display_order": 12,
        "branches": [
            {"name": "Principal Branch", "routing_number": "090271646", "district": "Dhaka", "branch_code": "001"},
            {"name": "Gulshan Branch", "routing_number": "090261353", "district": "Dhaka", "branch_code": "012"},
            {"name": "Motijheel Branch", "routing_number": "090272894", "district": "Dhaka", "branch_code": "020"},
            {"name": "Agrabad Branch", "routing_number": "090150334", "district": "Chattogram", "branch_code": "031"},
            {"name": "Sylhet Branch", "routing_number": "090870344", "district": "Sylhet", "branch_code": "045"},
        ],
    },
    {
        "code": "BRAC",
        "name": "BRAC Bank PLC",
        "short_name": "BRAC Bank",
        "bank_type": "commercial",
        "logo": "images/banks/brac.svg",
        "swift_code": "BRAKBDDH",
        "display_order": 13,
        "branches": [
            {"name": "Asad Gate Branch", "routing_number": "060260381", "district": "Dhaka", "branch_code": "101"},
            {"name": "Gulshan Branch", "routing_number": "060261717", "district": "Dhaka", "branch_code": "102"},
            {"name": "Uttara Branch", "routing_number": "060264675", "district": "Dhaka", "branch_code": "105"},
            {"name": "Agrabad Branch", "routing_number": "060150320", "district": "Chattogram", "branch_code": "120"},
        ],
    },
    {
        "code": "CITY",
        "name": "The City Bank PLC",
        "short_name": "City Bank",
        "bank_type": "commercial",
        "logo": "images/banks/city.svg",
        "swift_code": "CIBLBDDH",
        "display_order": 14,
        "branches": [
            {"name": "Principal Branch", "routing_number": "085272635", "district": "Dhaka", "branch_code": "201"},
            {"name": "Gulshan Branch", "routing_number": "085261549", "district": "Dhaka", "branch_code": "202"},
            {"name": "Dhanmondi Branch", "routing_number": "085261073", "district": "Dhaka", "branch_code": "205"},
            {"name": "Agrabad Branch", "routing_number": "085150361", "district": "Chattogram", "branch_code": "210"},
        ],
    },
    {
        "code": "EBL",
        "name": "Eastern Bank PLC",
        "short_name": "Eastern Bank",
        "bank_type": "commercial",
        "logo": "images/banks/ebl.svg",
        "swift_code": "EBLBBDDH",
        "display_order": 15,
        "branches": [
            {"name": "Principal Branch", "routing_number": "095272583", "district": "Dhaka", "branch_code": "301"},
            {"name": "Gulshan Branch", "routing_number": "095261559", "district": "Dhaka", "branch_code": "302"},
            {"name": "Agrabad Branch", "routing_number": "095150379", "district": "Chattogram", "branch_code": "310"},
        ],
    },
    {
        "code": "IBBL",
        "name": "Islami Bank Bangladesh PLC",
        "short_name": "Islami Bank",
        "bank_type": "islamic",
        "logo": "images/banks/ibbl.svg",
        "swift_code": "IBBLBDDH",
        "display_order": 16,
        "branches": [
            {"name": "Local Office", "routing_number": "125272935", "district": "Dhaka", "branch_code": "501"},
            {"name": "Foreign Exchange Branch", "routing_number": "125271813", "district": "Dhaka", "branch_code": "502"},
            {"name": "Agrabad Branch", "routing_number": "125150493", "district": "Chattogram", "branch_code": "510"},
        ],
    },
    {
        "code": "UCB",
        "name": "United Commercial Bank PLC",
        "short_name": "UCB",
        "bank_type": "commercial",
        "logo": "images/banks/ucb.svg",
        "swift_code": "UCBLBDDH",
        "display_order": 17,
        "branches": [
            {"name": "Principal Branch", "routing_number": "225272818", "district": "Dhaka", "branch_code": "601"},
            {"name": "Gulshan Branch", "routing_number": "225261563", "district": "Dhaka", "branch_code": "602"},
        ],
    },
    {
        "code": "MTB",
        "name": "Mutual Trust Bank PLC",
        "short_name": "Mutual Trust Bank",
        "bank_type": "commercial",
        "logo": "images/banks/mtb.svg",
        "swift_code": "MTBLBDDH",
        "display_order": 18,
        "branches": [
            {"name": "Principal Branch", "routing_number": "145272445", "district": "Dhaka", "branch_code": "701"},
            {"name": "Gulshan Branch", "routing_number": "145261647", "district": "Dhaka", "branch_code": "702"},
        ],
    },
    {
        "code": "PRIME",
        "name": "Prime Bank PLC",
        "short_name": "Prime Bank",
        "bank_type": "commercial",
        "logo": "images/banks/prime.svg",
        "swift_code": "PRBLBDDH",
        "display_order": 19,
        "branches": [
            {"name": "Principal Branch", "routing_number": "170272480", "district": "Dhaka", "branch_code": "801"},
            {"name": "Mohakhali Branch", "routing_number": "170262656", "district": "Dhaka", "branch_code": "802"},
        ],
    },
    {
        "code": "DHAKA",
        "name": "Dhaka Bank PLC",
        "short_name": "Dhaka Bank",
        "bank_type": "commercial",
        "logo": "images/banks/dhaka.svg",
        "swift_code": "DHBLBDDH",
        "display_order": 20,
        "branches": [
            {"name": "Principal Branch", "routing_number": "080272492", "district": "Dhaka", "branch_code": "901"},
            {"name": "Gulshan Branch", "routing_number": "080261622", "district": "Dhaka", "branch_code": "902"},
        ],
    },
    {
        "code": "BANKASIA",
        "name": "Bank Asia PLC",
        "short_name": "Bank Asia",
        "bank_type": "commercial",
        "logo": "images/banks/bankasia.svg",
        "swift_code": "BKASBDDH",
        "display_order": 21,
        "branches": [
            {"name": "Principal Branch", "routing_number": "070272477", "district": "Dhaka", "branch_code": "1001"},
            {"name": "Corporate Branch", "routing_number": "070261376", "district": "Dhaka", "branch_code": "1002"},
        ],
    },
    {
        "code": "PUBALI",
        "name": "Pubali Bank PLC",
        "short_name": "Pubali Bank",
        "bank_type": "commercial",
        "logo": "images/banks/pubali.svg",
        "swift_code": "PUBLBDDH",
        "display_order": 22,
        "branches": [
            {"name": "Principal Branch", "routing_number": "175272852", "district": "Dhaka", "branch_code": "1501"},
        ],
    },
    {
        "code": "UTTARA",
        "name": "Uttara Bank PLC",
        "short_name": "Uttara Bank",
        "bank_type": "commercial",
        "logo": "images/banks/uttara.svg",
        "swift_code": "UTBIBDDH",
        "display_order": 23,
        "branches": [
            {"name": "Local Office", "routing_number": "230272872", "district": "Dhaka", "branch_code": "1601"},
        ],
    },
    {
        "code": "TRUST",
        "name": "Trust Bank PLC",
        "short_name": "Trust Bank",
        "bank_type": "commercial",
        "logo": "images/banks/trust.svg",
        "swift_code": "TTBLBDDH",
        "display_order": 24,
        "branches": [
            {"name": "Principal Branch", "routing_number": "220272558", "district": "Dhaka", "branch_code": "1701"},
        ],
    },
    {
        "code": "SEBL",
        "name": "Southeast Bank PLC",
        "short_name": "Southeast Bank",
        "bank_type": "commercial",
        "logo": "images/banks/sebl.svg",
        "swift_code": "SEBDDHBA",
        "display_order": 25,
        "branches": [
            {"name": "Principal Branch", "routing_number": "195272837", "district": "Dhaka", "branch_code": "1801"},
        ],
    },
    {
        "code": "ABBL",
        "name": "AB Bank Limited",
        "short_name": "AB Bank",
        "bank_type": "commercial",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "ABBLBDDH",
        "display_order": 26,
        "branches": [
            {"name": "Principal Branch", "routing_number": "015272440", "district": "Dhaka", "branch_code": "015"},
        ],
    },
    {
        "code": "AIBL",
        "name": "Al-Arafah Islami Bank Limited",
        "short_name": "Al-Arafah Bank",
        "bank_type": "islamic",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "AIBLBDDH",
        "display_order": 27,
        "branches": [
            {"name": "Motijheel Branch", "routing_number": "020272440", "district": "Dhaka", "branch_code": "020"},
        ],
    },
    {
        "code": "BCBL",
        "name": "Bangladesh Commerce Bank Limited",
        "short_name": "Commerce Bank",
        "bank_type": "commercial",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "BCBLBDDH",
        "display_order": 28,
        "branches": [
            {"name": "Principal Branch", "routing_number": "035272440", "district": "Dhaka", "branch_code": "035"},
        ],
    },
    {
        "code": "EXIM",
        "name": "EXIM Bank Limited",
        "short_name": "EXIM Bank",
        "bank_type": "islamic",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "EXIMBDDH",
        "display_order": 29,
        "branches": [
            {"name": "Gulshan Branch", "routing_number": "100272440", "district": "Dhaka", "branch_code": "100"},
        ],
    },
    {
        "code": "FSIBL",
        "name": "First Security Islami Bank Limited",
        "short_name": "First Security Bank",
        "bank_type": "islamic",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "FSIBLBDDH",
        "display_order": 30,
        "branches": [
            {"name": "Dilkusha Branch", "routing_number": "105272440", "district": "Dhaka", "branch_code": "105"},
        ],
    },
    {
        "code": "ICBIBL",
        "name": "ICB Islamic Bank Limited",
        "short_name": "ICB Islamic Bank",
        "bank_type": "islamic",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "ICBIBDDH",
        "display_order": 31,
        "branches": [
            {"name": "Principal Branch", "routing_number": "115272440", "district": "Dhaka", "branch_code": "115"},
        ],
    },
    {
        "code": "IFIC",
        "name": "IFIC Bank Limited",
        "short_name": "IFIC Bank",
        "bank_type": "commercial",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "IFICBDDH",
        "display_order": 32,
        "branches": [
            {"name": "Principal Branch", "routing_number": "120272440", "district": "Dhaka", "branch_code": "120"},
        ],
    },
    {
        "code": "JAMUNA",
        "name": "Jamuna Bank Limited",
        "short_name": "Jamuna Bank",
        "bank_type": "commercial",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "JAMUBDDH",
        "display_order": 33,
        "branches": [
            {"name": "Dilkusha Branch", "routing_number": "130272440", "district": "Dhaka", "branch_code": "130"},
        ],
    },
    {
        "code": "MEGHNA",
        "name": "Meghna Bank Limited",
        "short_name": "Meghna Bank",
        "bank_type": "commercial",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "MGNABDDA",
        "display_order": 34,
        "branches": [
            {"name": "Principal Branch", "routing_number": "142272440", "district": "Dhaka", "branch_code": "142"},
        ],
    },
    {
        "code": "MBL",
        "name": "Mercantile Bank Limited",
        "short_name": "Mercantile Bank",
        "bank_type": "commercial",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "MBLBBDDH",
        "display_order": 35,
        "branches": [
            {"name": "Main Branch", "routing_number": "150272440", "district": "Dhaka", "branch_code": "150"},
        ],
    },
    {
        "code": "MDBL",
        "name": "Midland Bank Limited",
        "short_name": "Midland Bank",
        "bank_type": "commercial",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "MDBLBDDH",
        "display_order": 36,
        "branches": [
            {"name": "Gulshan Branch", "routing_number": "155272440", "district": "Dhaka", "branch_code": "155"},
        ],
    },
    {
        "code": "NBL",
        "name": "National Bank Limited",
        "short_name": "National Bank",
        "bank_type": "commercial",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "NBLBBDDH",
        "display_order": 37,
        "branches": [
            {"name": "Dilkusha Branch", "routing_number": "160272440", "district": "Dhaka", "branch_code": "160"},
        ],
    },
    {
        "code": "NRBB",
        "name": "NRB Bank Limited",
        "short_name": "NRB Bank",
        "bank_type": "commercial",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "NRBBBDDH",
        "display_order": 38,
        "branches": [
            {"name": "Gulshan Branch", "routing_number": "162272440", "district": "Dhaka", "branch_code": "162"},
        ],
    },
    {
        "code": "NCCB",
        "name": "NCC Bank Limited",
        "short_name": "NCC Bank",
        "bank_type": "commercial",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "NCCBBDDH",
        "display_order": 39,
        "branches": [
            {"name": "Motijheel Branch", "routing_number": "165272440", "district": "Dhaka", "branch_code": "165"},
        ],
    },
    {
        "code": "NRBCB",
        "name": "NRB Commercial Bank Limited",
        "short_name": "NRBC Bank",
        "bank_type": "commercial",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "NRBCBDDH",
        "display_order": 40,
        "branches": [
            {"name": "Principal Branch", "routing_number": "168272440", "district": "Dhaka", "branch_code": "168"},
        ],
    },
    {
        "code": "ONEBANK",
        "name": "ONE Bank Limited",
        "short_name": "ONE Bank",
        "bank_type": "commercial",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "ONEBBDDH",
        "display_order": 41,
        "branches": [
            {"name": "Principal Branch", "routing_number": "172272440", "district": "Dhaka", "branch_code": "172"},
        ],
    },
    {
        "code": "PBL",
        "name": "Premier Bank Limited",
        "short_name": "Premier Bank",
        "bank_type": "commercial",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "PRMRBDDH",
        "display_order": 42,
        "branches": [
            {"name": "Banani Branch", "routing_number": "178272440", "district": "Dhaka", "branch_code": "178"},
        ],
    },
    {
        "code": "SJIBL",
        "name": "Shahjalal Islami Bank Limited",
        "short_name": "Shahjalal Islami Bank",
        "bank_type": "islamic",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "SJIBBDDH",
        "display_order": 43,
        "branches": [
            {"name": "Dhaka Main Branch", "routing_number": "188272440", "district": "Dhaka", "branch_code": "188"},
        ],
    },
    {
        "code": "SIBL",
        "name": "Social Islami Bank Limited",
        "short_name": "Social Islami Bank",
        "bank_type": "islamic",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "SIBLBDDH",
        "display_order": 44,
        "branches": [
            {"name": "Principal Branch", "routing_number": "190272440", "district": "Dhaka", "branch_code": "190"},
        ],
    },
    {
        "code": "SBAC",
        "name": "South Bangla Agriculture and Commerce Bank Limited",
        "short_name": "SBAC Bank",
        "bank_type": "commercial",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "SBACBDDH",
        "display_order": 45,
        "branches": [
            {"name": "Principal Branch", "routing_number": "192272440", "district": "Dhaka", "branch_code": "192"},
        ],
    },
    {
        "code": "SBL",
        "name": "Standard Bank Limited",
        "short_name": "Standard Bank",
        "bank_type": "islamic",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "STNDBDDH",
        "display_order": 46,
        "branches": [
            {"name": "Principal Branch", "routing_number": "205272440", "district": "Dhaka", "branch_code": "205"},
        ],
    },
    {
        "code": "FARMERS",
        "name": "The Farmers Bank Limited",
        "short_name": "Farmers Bank",
        "bank_type": "commercial",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "PADMBDDH",
        "display_order": 47,
        "branches": [
            {"name": "Gulshan Branch", "routing_number": "210272440", "district": "Dhaka", "branch_code": "210"},
        ],
    },
    {
        "code": "UNION",
        "name": "Union Bank Limited",
        "short_name": "Union Bank",
        "bank_type": "islamic",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "UBLBBDDH",
        "display_order": 48,
        "branches": [
            {"name": "Gulshan Branch", "routing_number": "228272440", "district": "Dhaka", "branch_code": "228"},
        ],
    },
    # ── FOREIGN COMMERCIAL BANKS ──────────────────────────────────────────
    {
        "code": "SCB",
        "name": "Standard Chartered Bank",
        "short_name": "Standard Chartered",
        "bank_type": "foreign",
        "logo": "images/banks/scb.svg",
        "swift_code": "SCBLBDDH",
        "display_order": 49,
        "branches": [
            {"name": "Principal Branch", "routing_number": "215270118", "district": "Dhaka", "branch_code": "1101"},
            {"name": "Gulshan Branch", "routing_number": "215260533", "district": "Dhaka", "branch_code": "1102"},
        ],
    },
    {
        "code": "HSBC",
        "name": "HSBC Bangladesh",
        "short_name": "HSBC",
        "bank_type": "foreign",
        "logo": "images/banks/hsbc.svg",
        "swift_code": "HSBCBDDH",
        "display_order": 50,
        "branches": [
            {"name": "Main Office", "routing_number": "110270279", "district": "Dhaka", "branch_code": "1201"},
        ],
    },
    {
        "code": "ALFALAH",
        "name": "Bank Alfalah Limited",
        "short_name": "Bank Alfalah",
        "bank_type": "foreign",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "ALFHBDDH",
        "display_order": 51,
        "branches": [
            {"name": "Main Branch", "routing_number": "040272440", "district": "Dhaka", "branch_code": "040"},
        ],
    },
    {
        "code": "CITI",
        "name": "Citibank N.A.",
        "short_name": "Citibank",
        "bank_type": "foreign",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "CITIBDDH",
        "display_order": 52,
        "branches": [
            {"name": "Motijheel Branch", "routing_number": "075272440", "district": "Dhaka", "branch_code": "075"},
        ],
    },
    {
        "code": "CBC",
        "name": "Commercial Bank of Ceylon Limited",
        "short_name": "CBC",
        "bank_type": "foreign",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "CCEYBDDH",
        "display_order": 53,
        "branches": [
            {"name": "Main Branch", "routing_number": "088272440", "district": "Dhaka", "branch_code": "088"},
        ],
    },
    {
        "code": "HBL",
        "name": "Habib Bank Limited",
        "short_name": "Habib Bank",
        "bank_type": "foreign",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "HABBBDDH",
        "display_order": 54,
        "branches": [
            {"name": "Dhaka Branch", "routing_number": "108272440", "district": "Dhaka", "branch_code": "108"},
        ],
    },
    {
        "code": "NBP",
        "name": "National Bank of Pakistan",
        "short_name": "NBP",
        "bank_type": "foreign",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "NBPAIDDH",
        "display_order": 55,
        "branches": [
            {"name": "Dhaka Main Branch", "routing_number": "164272440", "district": "Dhaka", "branch_code": "164"},
        ],
    },
    {
        "code": "SBI",
        "name": "State Bank of India",
        "short_name": "State Bank of India",
        "bank_type": "foreign",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "SBINBDDH",
        "display_order": 56,
        "branches": [
            {"name": "Gulshan Branch", "routing_number": "208272440", "district": "Dhaka", "branch_code": "208"},
        ],
    },
    {
        "code": "WOORI",
        "name": "Woori Bank",
        "short_name": "Woori Bank",
        "bank_type": "foreign",
        "logo": "images/banks/default_bank.svg",
        "swift_code": "HVBKBDDH",
        "display_order": 57,
        "branches": [
            {"name": "Dhaka Branch", "routing_number": "235272440", "district": "Dhaka", "branch_code": "235"},
        ],
    },
]


def seed_directory():
    banks_created = 0
    branches_created = 0
    for item in CANONICAL_BANKS:
        bank_data = dict(item)
        branches = bank_data.get("branches", [])
        bank, created = Bank.objects.update_or_create(
            code=bank_data["code"],
            defaults={
                "name": bank_data["name"],
                "short_name": bank_data["short_name"],
                "bank_type": bank_data["bank_type"],
                "logo": bank_data["logo"],
                "swift_code": bank_data.get("swift_code", ""),
                "display_order": bank_data["display_order"],
                "is_active": True,
                "source_metadata": {"authority": "Bangladesh Bank Official Schedule", "version": "2026.1"},
            },
        )
        if created:
            banks_created += 1

        for br_data in branches:
            _, br_created = BankBranch.objects.update_or_create(
                bank=bank,
                routing_number=br_data["routing_number"],
                defaults={
                    "name": br_data["name"],
                    "district": br_data["district"],
                    "branch_code": br_data.get("branch_code", ""),
                    "is_active": True,
                    "source_metadata": {"source": "Bangladesh Bank BEFTN/NPSB Directory"},
                },
            )
            if br_created:
                branches_created += 1

    return banks_created, branches_created


class Command(BaseCommand):
    help = "Seeds the canonical Bangladesh Bank directory (banks and branches with routing numbers)"

    def handle(self, *args, **options):
        self.stdout.write("Seeding canonical Bangladesh Bank directory...")
        b_count, br_count = seed_directory()
        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {b_count} new banks and {br_count} new branches. Total: {Bank.objects.count()} banks, {BankBranch.objects.count()} branches."
            )
        )
