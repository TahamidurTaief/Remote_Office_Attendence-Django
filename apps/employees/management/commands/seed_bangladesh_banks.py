from django.core.management.base import BaseCommand
from apps.employees.models import Bank, BankBranch

CANONICAL_BANKS = [
    {
        "code": "DBBL",
        "name": "Dutch-Bangla Bank PLC",
        "short_name": "Dutch-Bangla Bank",
        "bank_type": "commercial",
        "logo": "images/banks/dbbl.svg",
        "swift_code": "DBBLBDDH",
        "display_order": 1,
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
        "display_order": 2,
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
        "display_order": 3,
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
        "display_order": 4,
        "branches": [
            {"name": "Principal Branch", "routing_number": "095272583", "district": "Dhaka", "branch_code": "301"},
            {"name": "Gulshan Branch", "routing_number": "095261559", "district": "Dhaka", "branch_code": "302"},
            {"name": "Agrabad Branch", "routing_number": "095150379", "district": "Chattogram", "branch_code": "310"},
        ],
    },
    {
        "code": "SONALI",
        "name": "Sonali Bank PLC",
        "short_name": "Sonali Bank",
        "bank_type": "state_owned",
        "logo": "images/banks/sonali.svg",
        "swift_code": "BSONBDDH",
        "display_order": 5,
        "branches": [
            {"name": "Local Office", "routing_number": "200270054", "district": "Dhaka", "branch_code": "401"},
            {"name": "Motijheel Branch", "routing_number": "200273763", "district": "Dhaka", "branch_code": "402"},
            {"name": "Laldighi Branch", "routing_number": "200153393", "district": "Chattogram", "branch_code": "410"},
        ],
    },
    {
        "code": "IBBL",
        "name": "Islami Bank Bangladesh PLC",
        "short_name": "Islami Bank",
        "bank_type": "islamic",
        "logo": "images/banks/ibbl.svg",
        "swift_code": "IBBLBDDH",
        "display_order": 6,
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
        "display_order": 7,
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
        "display_order": 8,
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
        "display_order": 9,
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
        "display_order": 10,
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
        "display_order": 11,
        "branches": [
            {"name": "Principal Branch", "routing_number": "070272477", "district": "Dhaka", "branch_code": "1001"},
            {"name": "Corporate Branch", "routing_number": "070261376", "district": "Dhaka", "branch_code": "1002"},
        ],
    },
    {
        "code": "SCB",
        "name": "Standard Chartered Bank",
        "short_name": "Standard Chartered",
        "bank_type": "foreign",
        "logo": "images/banks/scb.svg",
        "swift_code": "SCBLBDDH",
        "display_order": 12,
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
        "display_order": 13,
        "branches": [
            {"name": "Main Office", "routing_number": "110270279", "district": "Dhaka", "branch_code": "1201"},
        ],
    },
    {
        "code": "JANATA",
        "name": "Janata Bank PLC",
        "short_name": "Janata Bank",
        "bank_type": "state_owned",
        "logo": "images/banks/janata.svg",
        "swift_code": "JANBBDDH",
        "display_order": 14,
        "branches": [
            {"name": "Local Office", "routing_number": "135272458", "district": "Dhaka", "branch_code": "1301"},
        ],
    },
    {
        "code": "AGRANI",
        "name": "Agrani Bank PLC",
        "short_name": "Agrani Bank",
        "bank_type": "state_owned",
        "logo": "images/banks/agrani.svg",
        "swift_code": "AGRABDDA",
        "display_order": 15,
        "branches": [
            {"name": "Principal Branch", "routing_number": "010272844", "district": "Dhaka", "branch_code": "1401"},
        ],
    },
    {
        "code": "PUBALI",
        "name": "Pubali Bank PLC",
        "short_name": "Pubali Bank",
        "bank_type": "commercial",
        "logo": "images/banks/pubali.svg",
        "swift_code": "PUBLBDDH",
        "display_order": 16,
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
        "display_order": 17,
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
        "display_order": 18,
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
        "display_order": 19,
        "branches": [
            {"name": "Principal Branch", "routing_number": "195272837", "district": "Dhaka", "branch_code": "1801"},
        ],
    },
    {
        "code": "RUPALI",
        "name": "Rupali Bank PLC",
        "short_name": "Rupali Bank",
        "bank_type": "state_owned",
        "logo": "images/banks/rupali.svg",
        "swift_code": "RUPBBDDH",
        "display_order": 20,
        "branches": [
            {"name": "Local Office", "routing_number": "185272905", "district": "Dhaka", "branch_code": "1901"},
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
